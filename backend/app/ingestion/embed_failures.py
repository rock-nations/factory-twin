"""Describe each Failure in text (errors, sensor deviations) and embed it."""

import argparse
import logging
from pathlib import Path

import pandas as pd
from neo4j import Driver

from app.core.embeddings import Embedder, OpenAIEmbedder
from app.core.neo4j import create_sync_driver
from app.graph import constants as C
from app.ingestion.common import DEFAULT_RAW_DIR, read_pdm_csv, run_read, write_batches
from app.ingestion.load_events import failure_rows
from app.ingestion.telemetry_stats import TelemetryIndex, describe_window

logger = logging.getLogger(__name__)

LOOKBACK_H = 48
EMBED_BATCH = 200


def failure_description(
    component: str,
    model: str,
    machine: str,
    age: int,
    error_counts: dict[str, int],
    sensors: str,
) -> str:
    errors = ", ".join(f"{e} x{n}" for e, n in sorted(error_counts.items())) or "none"
    return (
        f"{component} failure on {model} machine {machine} (age {age} years). "
        f"Errors in the prior {LOOKBACK_H}h: {errors}. "
        f"Sensors in the prior {LOOKBACK_H}h: {sensors}."
    )


def build_descriptions(raw_dir: Path) -> dict[str, str]:
    machines = read_pdm_csv(raw_dir, "PdM_machines.csv").set_index("machineID")
    errors = read_pdm_csv(raw_dir, "PdM_errors.csv")
    errors["ts"] = pd.to_datetime(errors["datetime"])
    failures = read_pdm_csv(raw_dir, "PdM_failures.csv")
    index = TelemetryIndex(read_pdm_csv(raw_dir, "PdM_telemetry.csv"))
    errors_by_machine = {int(str(m)): g for m, g in errors.groupby("machineID")}

    out: dict[str, str] = {}
    for f in failure_rows(failures, set()):
        mid = int(f["machine"][1:])
        ts = pd.Timestamp(f["ts"])
        g = errors_by_machine.get(mid)
        counts: dict[str, int] = {}
        if g is not None:
            prior = g[(g["ts"] < ts) & (g["ts"] >= ts - pd.Timedelta(hours=LOOKBACK_H))]
            counts = {str(k): int(v) for k, v in prior["errorID"].value_counts().items()}
        out[f["id"]] = failure_description(
            C.COMPONENTS[f["comp"]],
            str(machines.loc[mid, "model"]),
            f["machine"],
            int(str(machines.loc[mid, "age"])),
            counts,
            describe_window(index.window(mid, ts, LOOKBACK_H)),
        )
    return out


def embed_failures(
    driver: Driver, embedder: Embedder, raw_dir: Path = DEFAULT_RAW_DIR
) -> dict[str, int]:
    descriptions = build_descriptions(raw_dir)
    current = {
        r["id"]: (r["desc"], r["has_emb"])
        for r in run_read(
            driver,
            "MATCH (f:Failure) RETURN f.id AS id, f.description AS desc, "
            "f.embedding IS NOT NULL AS has_emb",
        )
    }
    todo = [(fid, d) for fid, d in descriptions.items() if current.get(fid) != (d, True)]
    for start in range(0, len(todo), EMBED_BATCH):
        batch = todo[start : start + EMBED_BATCH]
        vectors = embedder.embed([d for _, d in batch])
        write_batches(
            driver,
            "UNWIND $rows AS row MATCH (f:Failure {id: row.id}) "
            "SET f.description = row.description, f.embedding = row.embedding",
            [
                {"id": fid, "description": d, "embedding": v}
                for (fid, d), v in zip(batch, vectors, strict=True)
            ],
        )
    stats = {
        "failures": len(descriptions),
        "embedded": len(todo),
        "skipped": len(descriptions) - len(todo),
    }
    logger.info("Failures: %s", stats)
    return stats


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    driver = create_sync_driver()
    try:
        print(embed_failures(driver, OpenAIEmbedder(), args.raw_dir))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
