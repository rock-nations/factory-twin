from pathlib import Path

import pytest
from neo4j import Driver

from app.core.config import Settings
from app.ingestion.build_topology import build_topology
from app.ingestion.common import run_read
from app.ingestion.embed_documents import embed_documents
from app.ingestion.embed_failures import build_descriptions, embed_failures
from app.ingestion.init_schema import init_schema
from app.ingestion.load_events import load_events
from tests.fakes import DIMS, HashingEmbedder

pytestmark = pytest.mark.integration

DOCS = {
    "sop_comp1.md": """---
doc_id: sop-comp1
doc_type: sop
title: 'SOP: Replacing the spindle bearing'
machine_model: null
component: comp1
error_type: null
failure_id: null
synthetic: true
---

# SOP: Replacing the spindle bearing

## Safety

Lock out the machine before touching the spindle bearing.

## Procedure

Remove the housing, press out the worn spindle bearing, fit the new bearing.
""",
    "troubleshooting_error3.md": """---
doc_id: troubleshooting-error3
doc_type: troubleshooting
title: 'Troubleshooting error3: Drive motor overload'
machine_model: null
component: null
error_type: error3
failure_id: null
synthetic: true
---

# Troubleshooting error3

## Likely causes

Drive motor overload from worn windings or a jammed coolant valve.
""",
    "manual_model2.md": """---
doc_id: manual-model2
doc_type: manual
title: Model 2 Operation and Maintenance Manual
machine_model: model2
component: null
error_type: null
failure_id: null
synthetic: true
---

# Model 2 manual

## Normal operating ranges

Pressure normal range for model2 is 100 bar with warning at 127 bar.
""",
}


@pytest.fixture
def base(clean_db: Driver, pdm_dir: Path, test_settings: Settings) -> Driver:
    init_schema(clean_db, test_settings)
    build_topology(clean_db, pdm_dir)
    load_events(clean_db, pdm_dir)
    return clean_db


@pytest.fixture
def docs_dir(tmp_path: Path) -> Path:
    for name, text in DOCS.items():
        (tmp_path / name).write_text(text)
    # incident report for a real failure from the fixture
    return tmp_path


def test_embed_then_rerun_embeds_nothing(base: Driver, docs_dir: Path) -> None:
    emb = HashingEmbedder()
    first = embed_documents(base, emb, docs_dir)
    assert first["embedded"] == 3 and first["skipped"] == 0
    calls = emb.texts_embedded
    second = embed_documents(base, emb, docs_dir)
    assert second["embedded"] == 0 and second["skipped"] == 3
    assert emb.texts_embedded == calls


def test_changed_document_is_reembedded_without_duplicate_chunks(
    base: Driver, docs_dir: Path
) -> None:
    emb = HashingEmbedder()
    embed_documents(base, emb, docs_dir)
    before = run_read(base, "MATCH (c:Chunk) RETURN count(c) AS n")[0]["n"]
    p = docs_dir / "sop_comp1.md"
    p.write_text(p.read_text() + "\n## Extra\n\nMore text about torque.\n")
    stats = embed_documents(base, emb, docs_dir)
    assert stats["embedded"] == 1
    assert run_read(base, "MATCH (c:Chunk) RETURN count(c) AS n")[0]["n"] >= before


def test_chunk_graph_links(base: Driver, docs_dir: Path) -> None:
    embed_documents(base, HashingEmbedder(), docs_dir)
    row = run_read(
        base,
        """
        MATCH (d:Document {id: 'manual-model2'})-[:HAS_CHUNK]->(c:Chunk)-[:ABOUT]->(m:MachineModel)
        RETURN d.doc_type AS t, m.id AS model, c.embedding IS NOT NULL AS emb,
               size(c.embedding) AS dims
        """,
    )[0]
    assert row == {"t": "manual", "model": "model2", "emb": True, "dims": DIMS}
    et = run_read(
        base,
        "MATCH (:Chunk)-[:ABOUT]->(e:ErrorType {id: 'error3'}) RETURN count(*) AS n",
    )[0]["n"]
    assert et >= 1
    doc = run_read(
        base, "MATCH (d:Document {id: 'sop-comp1'}) RETURN d.component AS c, d.synthetic AS s"
    )
    assert doc == [{"c": "comp1", "s": True}]


def test_incident_report_describes_failure_and_is_about_component(
    base: Driver, docs_dir: Path
) -> None:
    fid = run_read(base, "MATCH (f:Failure) RETURN f.id AS id ORDER BY id LIMIT 1")[0]["id"]
    (docs_dir / f"incident_{fid}.md").write_text(
        f"---\ndoc_id: incident-{fid}\ndoc_type: incident_report\ntitle: Incident {fid}\n"
        f"machine_model: model1\ncomponent: comp1\nerror_type: null\nfailure_id: {fid}\n"
        f"synthetic: true\n---\n\n# Incident {fid}\n\n## Summary\n\nA failure happened.\n"
    )
    embed_documents(base, HashingEmbedder(), docs_dir)
    rows = run_read(
        base,
        """
        MATCH (d:Document {id: $doc})-[:DESCRIBES]->(f:Failure {id: $fid})-[:FAILED]->(comp)
        MATCH (d)-[:HAS_CHUNK]->(:Chunk)-[:ABOUT]->(comp)
        RETURN count(*) AS n
        """,
        doc=f"incident-{fid}",
        fid=fid,
    )
    assert rows[0]["n"] >= 1


def test_vector_query_finds_spindle_bearing_content_in_top3(base: Driver, docs_dir: Path) -> None:
    emb = HashingEmbedder()
    embed_documents(base, emb, docs_dir)
    q = emb.embed(["spindle bearing worn replace"])[0]
    rows = run_read(
        base,
        """
        CALL db.index.vector.queryNodes('chunk_embedding', 3, $q) YIELD node, score
        MATCH (d:Document)-[:HAS_CHUNK]->(node)
        RETURN d.id AS doc, score
        """,
        q=q,
    )
    assert "sop-comp1" in {r["doc"] for r in rows}


def test_embed_failures_idempotent(base: Driver, pdm_dir: Path) -> None:
    emb = HashingEmbedder()
    first = embed_failures(base, emb, pdm_dir)
    assert first["embedded"] == first["failures"] > 0
    second = embed_failures(base, emb, pdm_dir)
    assert second["embedded"] == 0
    n = run_read(base, "MATCH (f:Failure) WHERE f.embedding IS NOT NULL RETURN count(f) AS n")
    assert n[0]["n"] == first["failures"]


def test_failure_descriptions_are_informative(pdm_dir: Path) -> None:
    descs = build_descriptions(pdm_dir)
    d = next(iter(descs.values()))
    assert (
        " failure on model" in d and "Errors in the prior 48h" in d and "Sensors in the prior" in d
    )
