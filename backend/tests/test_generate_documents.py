from pathlib import Path

import frontmatter
import pytest

from app.graph import constants as C
from app.ingestion.compute_thresholds import compute_thresholds
from app.ingestion.generate_documents import (
    Inputs,
    build_specs,
    dry_run,
    generate,
    render_document,
)
from tests.fakes import FakeLLM


@pytest.fixture(scope="module")
def inp(pdm_dir: Path) -> Inputs:
    return Inputs(pdm_dir)


def test_counts_per_type(inp: Inputs) -> None:
    specs = build_specs(inp, C.DOC_TYPES, limit=5)
    by_type = {t: sum(1 for s in specs if s.doc_type == t) for t in C.DOC_TYPES}
    assert by_type == {"manual": 4, "troubleshooting": 5, "sop": 4, "incident_report": 5}


def test_file_names_and_ids_are_deterministic(inp: Inputs) -> None:
    a = build_specs(inp, C.DOC_TYPES, limit=5)
    b = build_specs(inp, C.DOC_TYPES, limit=5)
    assert [(s.filename, s.doc_id) for s in a] == [(s.filename, s.doc_id) for s in b]
    assert {"manual_model3.md", "sop_comp1.md", "troubleshooting_error3.md"} <= {
        s.filename for s in a
    }


def test_manual_ranges_match_computed_thresholds(inp: Inputs) -> None:
    thresholds = compute_thresholds(inp.telemetry)
    manual = build_specs(inp, ["manual"], 0)[0]
    for sensor, t in thresholds.items():
        facts = manual.facts["sensors"][sensor]
        assert facts["warning_high"] == round(t["warn_high"] or 0, 1)
        assert facts["critical_high"] == round(t["crit_high"] or 0, 1)


def test_documents_use_the_shared_component_names(inp: Inputs) -> None:
    manual = build_specs(inp, ["manual"], 0)[0]
    assert set(manual.facts["components"]) == set(C.COMPONENTS.values())
    sop = {s.doc_id: s for s in build_specs(inp, ["sop"], 0)}["sop-comp1"]
    assert sop.facts["component"] == "Spindle bearing"


def test_incident_facts_come_from_real_rows(inp: Inputs) -> None:
    specs = build_specs(inp, ["incident_report"], limit=3)
    assert len(specs) == 3
    for s in specs:
        assert s.meta["failure_id"].startswith("F-") and s.meta["component"] in C.COMPONENTS
        assert s.facts["repaired_by"] in C.TECHNICIAN_NAMES
        assert 2 <= s.facts["downtime_hours"] <= 12


def test_render_has_front_matter_and_title(inp: Inputs) -> None:
    spec = build_specs(inp, ["sop"], 0)[0]
    post = frontmatter.loads(render_document(spec.meta, "## Scope\n\ntext"))
    assert post["synthetic"] is True and post["doc_type"] == "sop" and post["component"] == "comp1"
    assert post.content.startswith(f"# {spec.title}")


def test_generate_skips_existing_files(inp: Inputs, tmp_path: Path) -> None:
    specs = build_specs(inp, ["sop"], 0)
    llm = FakeLLM()
    n, usage = generate(specs, tmp_path, llm, "cheap-model", workers=2)
    assert n == 4 and usage.input_tokens == 400 and len(llm.calls) == 4
    n2, _ = generate(specs, tmp_path, llm, "cheap-model", workers=2)
    assert n2 == 0 and len(llm.calls) == 4


def test_dry_run_makes_no_calls_and_reports(
    inp: Inputs, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dry_run(build_specs(inp, C.DOC_TYPES, limit=5), tmp_path)
    out = capsys.readouterr().out
    assert "18 documents planned" in out and "to generate" in out


def test_followup_counts_are_distinct_failures_not_error_failure_pairs(inp: Inputs) -> None:
    total_by_comp = inp.failures["failure"].map(C.COMPONENTS).value_counts().to_dict()
    for spec in build_specs(inp, ["troubleshooting"], 0):
        followed = spec.facts["components_failed_within_7_days_after"]
        for comp, n in followed.items():
            assert n <= total_by_comp[comp], (
                f"{spec.doc_id}: {comp} {n} > {total_by_comp[comp]} failures exist"
            )
