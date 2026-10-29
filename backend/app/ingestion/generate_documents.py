"""LLM-written RAG documents grounded in the real data (spec §4.3).

    python -m app.ingestion.generate_documents --dry-run
    python -m app.ingestion.generate_documents --limit 120 --types manual,sop

Deterministic file names; existing files are skipped so re-runs never re-spend.
"""

import argparse
import json
import logging
import random
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from app.core.config import get_settings
from app.core.llm import LLM, ConfigError, TextCompleter, Usage, estimate_cost, require_setting
from app.graph import constants as C
from app.ingestion.chunker import count_tokens
from app.ingestion.common import (
    DEFAULT_GENERATED_DIR,
    DEFAULT_RAW_DIR,
    read_pdm_csv,
)
from app.ingestion.compute_thresholds import compute_thresholds
from app.ingestion.load_events import failure_rows, maintenance_rows
from app.ingestion.telemetry_stats import SENSORS, TelemetryIndex, describe_window, notable

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
# rough completion length per doc type, only used for --dry-run cost estimates
EXPECTED_OUTPUT_TOKENS = {
    "manual": 1000,
    "troubleshooting": 800,
    "sop": 900,
    "incident_report": 500,
}
PROMPT_FILES = {
    "manual": "docgen_manual.md",
    "troubleshooting": "docgen_troubleshooting.md",
    "sop": "docgen_sop.md",
    "incident_report": "docgen_incident.md",
}
DEFAULT_INCIDENT_LIMIT = 120
ERROR_LOOKBACK_H = 24
INCIDENT_LOOKBACK_H = 48
FOLLOWUP_DAYS = 7


@dataclass
class DocSpec:
    doc_id: str
    doc_type: str
    title: str
    filename: str
    meta: dict[str, Any]
    facts: dict[str, Any] = field(repr=False)


def _meta(doc_id: str, doc_type: str, title: str, **extra: str | None) -> dict[str, Any]:
    base: dict[str, Any] = {
        "doc_id": doc_id,
        "doc_type": doc_type,
        "title": title,
        "machine_model": None,
        "component": None,
        "error_type": None,
        "failure_id": None,
    }
    base.update(extra)
    base["synthetic"] = True
    return base


def render_document(meta: dict[str, Any], body: str) -> str:
    body = body.strip()
    if not body.startswith("# "):
        body = f"# {meta['title']}\n\n{body}"
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{front}\n---\n\n{body}\n"


class Inputs:
    """Everything the document facts are derived from."""

    def __init__(self, raw_dir: Path) -> None:
        self.machines = read_pdm_csv(raw_dir, "PdM_machines.csv")
        self.telemetry = read_pdm_csv(raw_dir, "PdM_telemetry.csv")
        self.errors = read_pdm_csv(raw_dir, "PdM_errors.csv")
        self.maint = read_pdm_csv(raw_dir, "PdM_maint.csv")
        self.failures = read_pdm_csv(raw_dir, "PdM_failures.csv")
        self.thresholds = compute_thresholds(self.telemetry)
        self.index = TelemetryIndex(self.telemetry)


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


def manual_specs(inp: Inputs) -> list[DocSpec]:
    specs = []
    for model in C.MODELS:
        ms = inp.machines[inp.machines["model"] == model]
        title = f"Model {model[-1]} Operation and Maintenance Manual"
        facts = {
            "title": title,
            "machine_model": model,
            "machines_in_fleet": len(ms),
            "age_years": {
                "min": int(ms["age"].min()),
                "max": int(ms["age"].max()),
                "mean": round(float(ms["age"].mean()), 1),
            },
            "sensors": {
                s: {
                    "unit": C.SENSOR_UNITS[s],
                    "normal_mean": _round(t["mean"]),
                    "warning_high": _round(t["warn_high"]),
                    "critical_high": _round(t["crit_high"]),
                    "warning_low": _round(t["warn_low"]),
                    "critical_low": _round(t["crit_low"]),
                }
                for s, t in inp.thresholds.items()
            },
            "components": {
                C.COMPONENTS[c]: {"replacement_interval_days": d}
                for c, d in C.MAINTENANCE_INTERVAL_DAYS.items()
            },
        }
        specs.append(
            DocSpec(
                f"manual-{model}",
                "manual",
                title,
                f"manual_{model}.md",
                _meta(f"manual-{model}", "manual", title, machine_model=model),
                facts,
            )
        )
    return specs


def troubleshooting_specs(inp: Inputs) -> list[DocSpec]:
    errors = inp.errors.assign(ts=pd.to_datetime(inp.errors["datetime"]))
    failures = inp.failures.assign(ts=pd.to_datetime(inp.failures["datetime"]))
    specs = []
    for code, desc in C.ERROR_TYPES.items():
        ev = errors[errors["errorID"] == code]
        sig: dict[str, list[float]] = {s: [] for s in SENSORS}
        for row in ev.itertuples():
            for w in inp.index.window(int(row.machineID), row.ts, ERROR_LOOKBACK_H):  # type: ignore[arg-type]
                sig[w.sensor].append(w.mean_z)
        merged = ev.merge(failures, on="machineID", suffixes=("_e", "_f"))
        delta = merged["ts_f"] - merged["ts_e"]
        follow = merged[(delta > pd.Timedelta(0)) & (delta <= pd.Timedelta(days=FOLLOWUP_DAYS))]
        # one failure can follow several errors; count each failure once
        follow = follow.drop_duplicates(subset=["machineID", "ts_f", "failure"])
        title = f"Troubleshooting {code}: {desc}"
        facts = {
            "title": title,
            "error_code": code,
            "description": desc,
            "events_in_history": len(ev),
            "mean_sensor_deviation_sigma_24h_before": {
                s: round(sum(v) / len(v), 2) if v else None for s, v in sig.items()
            },
            f"components_failed_within_{FOLLOWUP_DAYS}_days_after": {
                C.COMPONENTS[str(c)]: int(n) for c, n in follow["failure"].value_counts().items()
            },
        }
        specs.append(
            DocSpec(
                f"troubleshooting-{code}",
                "troubleshooting",
                title,
                f"troubleshooting_{code}.md",
                _meta(f"troubleshooting-{code}", "troubleshooting", title, error_type=code),
                facts,
            )
        )
    return specs


def sop_specs(inp: Inputs) -> list[DocSpec]:
    specs = []
    for comp, name in C.COMPONENTS.items():
        title = f"SOP: Replacing the {name.lower()}"
        facts = {
            "title": title,
            "component": name,
            "component_type": comp,
            "recommended_replacement_interval_days": C.MAINTENANCE_INTERVAL_DAYS[comp],
            "time_estimate_hours": C.REPLACEMENT_HOURS[comp],
            "applies_to_models": C.MODELS,
        }
        specs.append(
            DocSpec(
                f"sop-{comp}",
                "sop",
                title,
                f"sop_{comp}.md",
                _meta(f"sop-{comp}", "sop", title, component=comp),
                facts,
            )
        )
    return specs


def incident_specs(inp: Inputs, limit: int = DEFAULT_INCIDENT_LIMIT) -> list[DocSpec]:
    mnt = maintenance_rows(inp.maint, inp.failures)
    maint_by_id = {m["id"]: m for m in mnt}
    fails = failure_rows(inp.failures, set(maint_by_id))
    if limit < len(fails):
        keep = sorted(random.Random(C.SEED).sample(range(len(fails)), limit))
        fails = [fails[i] for i in keep]
    machines = inp.machines.set_index("machineID")
    errors = inp.errors.assign(ts=pd.to_datetime(inp.errors["datetime"]))
    tech_names = {f"T{i:02d}": n for i, n in enumerate(C.TECHNICIAN_NAMES, start=1)}

    specs = []
    for f in fails:
        mid = int(str(f["machine"])[1:])
        ts = pd.Timestamp(f["ts"])
        comp_name = C.COMPONENTS[f["comp"]]
        window = inp.index.window(mid, ts, INCIDENT_LOOKBACK_H)
        prior = errors[
            (errors["machineID"] == mid)
            & (errors["ts"] < ts)
            & (errors["ts"] >= ts - pd.Timedelta(hours=INCIDENT_LOOKBACK_H))
        ]
        maint = maint_by_id.get(f["maint_id"]) if f["maint_id"] else None
        downtime = round(random.Random(f"{C.SEED}-{f['id']}").uniform(2, 12) * 2) / 2
        title = f"Incident {f['id']}: {comp_name} failure on {f['machine']}"
        model = str(machines.loc[mid, "model"])
        facts = {
            "title": title,
            "failure_id": f["id"],
            "machine_id": f["machine"],
            "machine_model": model,
            "machine_age_years": int(str(machines.loc[mid, "age"])),
            "failed_component": comp_name,
            "failure_time": f["ts"],
            "errors_in_prior_48h": [
                {"error": r.errorID, "at": str(r.ts)} for r in prior.itertuples()
            ],
            "sensor_deviations_prior_48h": describe_window(window),
            "sensor_detail_prior_48h": [
                {
                    "sensor": w.sensor,
                    "mean_sigma": round(w.mean_z, 2),
                    "peak_sigma": round(w.peak_z, 2),
                }
                for w in notable(window)
            ],
            "repaired_by": tech_names[maint["technician"]] if maint else None,
            "downtime_hours": downtime,
        }
        specs.append(
            DocSpec(
                f"incident-{f['id']}",
                "incident_report",
                title,
                f"incident_{f['id']}.md",
                _meta(
                    f"incident-{f['id']}",
                    "incident_report",
                    title,
                    machine_model=model,
                    component=f["comp"],
                    failure_id=f["id"],
                ),
                facts,
            )
        )
    return specs


def build_specs(inp: Inputs, types: list[str], limit: int) -> list[DocSpec]:
    builders: dict[str, Callable[[], list[DocSpec]]] = {
        "manual": lambda: manual_specs(inp),
        "troubleshooting": lambda: troubleshooting_specs(inp),
        "sop": lambda: sop_specs(inp),
        "incident_report": lambda: incident_specs(inp, limit),
    }
    return [s for t in types for s in builders[t]()]


def load_prompt(doc_type: str) -> str:
    return (PROMPTS_DIR / PROMPT_FILES[doc_type]).read_text()


def user_message(spec: DocSpec) -> str:
    return "Facts (JSON):\n" + json.dumps(spec.facts, indent=2, ensure_ascii=False)


def dry_run(specs: list[DocSpec], out_dir: Path) -> None:
    s = get_settings()
    todo = [sp for sp in specs if not (out_dir / sp.filename).exists()]
    in_tok = sum(count_tokens(load_prompt(sp.doc_type) + user_message(sp)) for sp in todo)
    out_tok = sum(EXPECTED_OUTPUT_TOKENS[sp.doc_type] for sp in todo)
    cost = estimate_cost(Usage(in_tok, out_tok), s)
    print(
        f"{len(specs)} documents planned, {len(specs) - len(todo)} already exist, "
        f"{len(todo)} to generate"
    )
    for t in C.DOC_TYPES:
        print(f"  {t:<16}{sum(1 for sp in todo if sp.doc_type == t):>4}")
    print(f"estimated tokens: ~{in_tok:,} in / ~{out_tok:,} out")
    if s.price_input_per_m or s.price_output_per_m:
        print(
            f"estimated cost (PRICE_*_PER_M, model {s.openai_cheap_model or 'unset'}): ${cost:.3f}"
        )
    else:
        print("set PRICE_INPUT_PER_M / PRICE_OUTPUT_PER_M in .env to see a cost estimate")


def generate(
    specs: list[DocSpec], out_dir: Path, llm: TextCompleter, model: str, workers: int = 4
) -> tuple[int, Usage]:
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [sp for sp in specs if not (out_dir / sp.filename).exists()]

    def one(spec: DocSpec) -> Usage:
        text, usage = llm.complete(model, load_prompt(spec.doc_type), user_message(spec))
        (out_dir / spec.filename).write_text(render_document(spec.meta, text))
        logger.info("wrote %s (%d in / %d out tokens)", spec.filename, *vars(usage).values())
        return usage

    total = Usage()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for usage in pool.map(one, todo):
            total += usage
    return len(todo), total


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--limit", type=int, default=DEFAULT_INCIDENT_LIMIT, help="incident reports")
    p.add_argument("--types", default=",".join(C.DOC_TYPES))
    p.add_argument("--dry-run", action="store_true", help="plan + estimate cost, no API calls")
    p.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_GENERATED_DIR / "docs")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    types = [t.strip() for t in args.types.split(",") if t.strip()]
    if bad := set(types) - set(C.DOC_TYPES):
        p.error(f"unknown types {sorted(bad)}; choose from {C.DOC_TYPES}")
    specs = build_specs(Inputs(args.raw_dir), types, args.limit)
    if args.dry_run:
        dry_run(specs, args.out_dir)
        return
    s = get_settings()
    try:
        model = require_setting(s.openai_cheap_model, "OPENAI_CHEAP_MODEL")
        n, usage = generate(specs, args.out_dir, LLM(s), model)
    except ConfigError as exc:
        p.error(str(exc))
    print(
        f"generated {n} documents; {usage.input_tokens:,} in / {usage.output_tokens:,} out tokens; "
        f"~${estimate_cost(usage, s):.3f}"
    )


if __name__ == "__main__":
    main()
