from pathlib import Path
from typing import Any

import pytest
from neo4j import READ_ACCESS, Driver
from neo4j.exceptions import ClientError

from app.core.config import Settings
from app.core.llm import Usage
from app.ingestion.build_topology import build_topology
from app.ingestion.common import run_read, run_write
from app.ingestion.embed_documents import embed_documents
from app.ingestion.embed_failures import embed_failures
from app.ingestion.init_schema import init_schema
from app.ingestion.load_events import load_events
from app.retrieval.cypher_guard import run_guarded
from app.retrieval.graphrag import IncidentFinder
from app.retrieval.machine_context import get_machine_context, normalize_machine_id
from app.retrieval.text2cypher import (
    Text2Cypher,
    build_system_prompt,
    extract_cypher,
    load_examples,
)
from app.retrieval.vector_rag import KnowledgeBase, lucene_safe
from tests.fakes import HashingEmbedder
from tests.test_embed_integration import DOCS

pytestmark = pytest.mark.integration


@pytest.fixture
def world(
    clean_db: Driver, pdm_dir: Path, test_settings: Settings, tmp_path: Path
) -> tuple[Driver, HashingEmbedder]:
    init_schema(clean_db, test_settings)
    build_topology(clean_db, pdm_dir)
    load_events(clean_db, pdm_dir)
    docs = tmp_path / "docs"
    docs.mkdir()
    for name, text in DOCS.items():
        (docs / name).write_text(text)
    emb = HashingEmbedder()
    embed_documents(clean_db, emb, docs)
    embed_failures(clean_db, emb, pdm_dir)
    return clean_db, emb


# ---- guarded execution -------------------------------------------------------------------------


def test_every_few_shot_example_passes_the_guard_and_executes(world: tuple[Driver, Any]) -> None:
    driver, _ = world
    examples = load_examples()
    assert len(examples) >= 25
    for e in examples:
        res = run_guarded(driver, e["cypher"])
        assert res.error is None, f"{e['question']!r}: {res.error}"


def test_rows_are_capped_and_flagged_truncated(world: tuple[Driver, Any]) -> None:
    res = run_guarded(world[0], "MATCH (m:Machine) RETURN m.id AS id ORDER BY id LIMIT 1000")
    assert res.row_count == 50 and res.truncated


def test_limit_is_appended_automatically(world: tuple[Driver, Any]) -> None:
    res = run_guarded(world[0], "MATCH (m:Machine) RETURN m.id AS id ORDER BY id")
    assert res.row_count == 50 and "LIMIT 50" in res.cypher


def test_write_attempt_is_rejected_and_changes_nothing(world: tuple[Driver, Any]) -> None:
    driver, _ = world
    before = run_read(driver, "MATCH (n) RETURN count(n) AS n")
    res = run_guarded(driver, "MATCH (m:Machine {id: 'M001'}) SET m.status = 'down' RETURN m")
    assert res.error and "guard" in res.error
    assert run_read(driver, "MATCH (n) RETURN count(n) AS n") == before
    assert run_read(driver, "MATCH (m:Machine {id: 'M001'}) RETURN m.status AS s") == [
        {"s": "running"}
    ]


def test_read_transaction_blocks_writes_even_without_the_guard(world: tuple[Driver, Any]) -> None:
    driver, _ = world
    with driver.session(default_access_mode=READ_ACCESS) as session, pytest.raises(ClientError):
        session.execute_read(lambda tx: tx.run("CREATE (:Hacked)").consume())
    assert run_read(driver, "MATCH (h:Hacked) RETURN count(h) AS n") == [{"n": 0}]


def test_syntax_error_is_reported_not_raised(world: tuple[Driver, Any]) -> None:
    res = run_guarded(world[0], "MATCH (m:Machine RETURN m")
    assert res.error and "Syntax" in res.error


def test_values_are_json_safe(world: tuple[Driver, Any]) -> None:
    res = run_guarded(world[0], "MATCH (f:Failure) RETURN f.ts AS ts, f ORDER BY f.id LIMIT 1")
    row = res.rows[0]
    assert isinstance(row["ts"], str) and row["ts"].startswith("2015")
    assert row["f"]["id"].startswith("F-") and "embedding" not in row["f"]


# ---- text2cypher -------------------------------------------------------------------------------


class ScriptedLLM:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self, model: str, system: str, user: Any, max_output_tokens: int = 0
    ) -> tuple[str, Usage]:
        self.calls.append({"model": model, "system": system, "user": user})
        return self.replies.pop(0), Usage(10, 5)


def _t2c(driver: Driver, llm: ScriptedLLM, settings: Settings) -> Text2Cypher:
    return Text2Cypher(driver, llm, settings.model_copy(update={"openai_cypher_model": "m"}))


def test_text2cypher_happy_path(world: tuple[Driver, Any], test_settings: Settings) -> None:
    llm = ScriptedLLM("```cypher\nMATCH (p:Plant) RETURN p.id AS plant ORDER BY plant\n```")
    res = _t2c(world[0], llm, test_settings).query("list plants")
    assert res.error is None and res.attempts == 1
    assert [r["plant"] for r in res.rows] == ["P1", "P2"]
    assert (res.input_tokens, res.output_tokens) == (10, 5)
    assert "Schema" in llm.calls[0]["system"] and "M017" in llm.calls[0]["system"]


def test_text2cypher_retries_once_with_the_error_fed_back(
    world: tuple[Driver, Any], test_settings: Settings
) -> None:
    llm = ScriptedLLM(
        "```cypher\nMATCH (p:Plant RETURN p\n```",
        "```cypher\nMATCH (p:Plant) RETURN count(p) AS plants\n```",
    )
    res = _t2c(world[0], llm, test_settings).query("how many plants")
    assert res.attempts == 2 and res.rows == [{"plants": 2}]
    retry_messages = llm.calls[1]["user"]
    assert retry_messages[-1]["role"] == "user" and "failed with" in retry_messages[-1]["content"]


def test_text2cypher_gives_up_after_one_retry(
    world: tuple[Driver, Any], test_settings: Settings
) -> None:
    llm = ScriptedLLM("MATCH (p RETURN p", "MATCH (p RETURN p")
    res = _t2c(world[0], llm, test_settings).query("broken")
    assert res.attempts == 2 and res.error and not llm.replies


def test_text2cypher_guard_rejection_is_retried_too(
    world: tuple[Driver, Any], test_settings: Settings
) -> None:
    llm = ScriptedLLM("MATCH (m:Machine) DETACH DELETE m", "MATCH (m:Machine) RETURN count(m) AS n")
    res = _t2c(world[0], llm, test_settings).query("delete everything")
    assert res.attempts == 2 and res.rows == [{"n": 100}]
    assert run_read(world[0], "MATCH (m:Machine) RETURN count(m) AS n") == [{"n": 100}]


def test_text2cypher_no_query(world: tuple[Driver, Any], test_settings: Settings) -> None:
    res = _t2c(world[0], ScriptedLLM("NO_QUERY"), test_settings).query("how do I fix a pump?")
    assert res.error and "cannot be answered" in res.error and res.attempts == 1


def test_empty_result_is_explicit(world: tuple[Driver, Any], test_settings: Settings) -> None:
    llm = ScriptedLLM("MATCH (m:Machine {id: 'M999'}) RETURN m.id AS id")
    res = _t2c(world[0], llm, test_settings).query("machine 999")
    assert res.error is None and res.row_count == 0 and res.rows == []


def test_text2cypher_requires_cypher_model(world: tuple[Driver, Any]) -> None:
    from app.core.llm import ConfigError

    with pytest.raises(ConfigError, match="OPENAI_CYPHER_MODEL"):
        Text2Cypher(world[0], ScriptedLLM(), Settings(_env_file=None)).query("x")


def test_prompt_prefix_is_stable() -> None:
    assert build_system_prompt() is build_system_prompt()
    assert extract_cypher("blah ```cypher\nRETURN 1\n``` more") == "RETURN 1"
    assert extract_cypher("RETURN 2") == "RETURN 2"


# ---- machine context ---------------------------------------------------------------------------


def test_normalize_machine_id() -> None:
    assert [normalize_machine_id(x) for x in ["17", "m17", "Machine 017", "M100", "pump"]] == [
        "M017",
        "M017",
        "M017",
        "M100",
        None,
    ]


def test_machine_context_structure(world: tuple[Driver, Any]) -> None:
    driver, _ = world
    run_write(
        driver,
        """
        MATCH (m:Machine {id: 'M005'})-[:HAS_SENSOR]->(s:Sensor {type: 'vibration'})
        SET s.latest_value = 999.0, s.warn_high = 50.0, s.crit_high = 60.0
        CREATE (a:Alarm {id: 'AL-1', status: 'open', severity: 'critical', sensor_type: 'vibration',
                         value: 999.0, threshold: 60.0, ts: datetime(), message: 'x'})
        CREATE (a)-[:RAISED_ON]->(m)
        """,
    )
    ctx = get_machine_context(driver, "5")
    assert ctx["machine"]["id"] == "M005" and ctx["machine"]["line"] == "A1"
    assert {s["sensor"] for s in ctx["sensors"]} == {"volt", "rotate", "pressure", "vibration"}
    states = {s["sensor"]: s["state"] for s in ctx["sensors"]}
    assert states["vibration"] == "critical" and states["volt"] == "no_data"
    assert [a["alarm"] for a in ctx["open_alarms"]] == ["AL-1"]
    assert len(ctx["components"]) == 4
    assert {d["machine"] for d in ctx["downstream"]} >= {"M006"}
    assert {u["machine"] for u in ctx["upstream"]} >= {"M004"}


def test_machine_context_errors(world: tuple[Driver, Any]) -> None:
    assert "not found" in get_machine_context(world[0], "M999")["error"]
    assert "not a machine id" in get_machine_context(world[0], "pump")["error"]


# ---- vector / hybrid RAG -----------------------------------------------------------------------


def test_lucene_safe_strips_query_syntax() -> None:
    assert lucene_safe('How do I replace (a) spindle-bearing? "now"') == (
        "How do I replace a spindle bearing now"
    )


def test_hybrid_search_finds_relevant_chunk_with_sources(world: tuple[Driver, Any]) -> None:
    driver, emb = world
    kb = KnowledgeBase(driver, emb, Settings(_env_file=None, rag_min_score=0.6))
    res = kb.search("How do I replace a worn spindle bearing?")
    assert not res.no_relevant_documents
    assert res.chunks[0].doc_id == "sop-comp1"
    assert res.sources[0].title.startswith("SOP") and res.chunks[0].similarity >= 0.6


def test_search_filters_by_doc_type_and_model(world: tuple[Driver, Any]) -> None:
    driver, emb = world
    kb = KnowledgeBase(driver, emb, Settings(_env_file=None, rag_min_score=0.5))
    manuals = kb.search("pressure normal range model2", doc_types=["manual"])
    assert {c.doc_type for c in manuals.chunks} == {"manual"}
    other_model = kb.search("pressure normal range", doc_types=["manual"], machine_model="model4")
    assert other_model.no_relevant_documents  # the only manual in the fixture is for model2


def test_search_returns_no_relevant_documents_instead_of_guessing(
    world: tuple[Driver, Any],
) -> None:
    driver, emb = world
    kb = KnowledgeBase(driver, emb, Settings(_env_file=None, rag_min_score=0.75))
    res = kb.search("what is the capital of australia and who won the 1998 world cup")
    assert res.no_relevant_documents and res.chunks == [] and res.message


def test_search_survives_special_characters(world: tuple[Driver, Any]) -> None:
    driver, emb = world
    kb = KnowledgeBase(driver, emb, Settings(_env_file=None, rag_min_score=0.5))
    kb.search('error3: what (exactly) does "overload" mean? [help] a+b -c')


def test_chunks_carry_neighbours_and_about(world: tuple[Driver, Any]) -> None:
    driver, emb = world
    kb = KnowledgeBase(driver, emb, Settings(_env_file=None, rag_min_score=0.5))
    res = kb.search("error3 drive motor overload", doc_types=["troubleshooting"])
    assert "ErrorType:error3" in res.chunks[0].about


# ---- similar incidents -------------------------------------------------------------------------


def test_similar_incidents_return_graph_context(world: tuple[Driver, Any]) -> None:
    driver, emb = world
    f = run_read(driver, "MATCH (f:Failure) RETURN f.description AS d LIMIT 1")[0]["d"]
    res = IncidentFinder(driver, emb).find(f)
    assert res.incidents and res.incidents[0].similarity > 0.9
    top = res.incidents[0]
    assert top.machine_id.startswith("M") and top.component and top.model.startswith("model")
    assert top.resolution_reason == "failure" and top.technician


def test_similar_incidents_model_filter(world: tuple[Driver, Any]) -> None:
    driver, emb = world
    res = IncidentFinder(driver, emb).find("vibration spindle bearing failure", "model1")
    assert res.incidents and all(i.model == "model1" for i in res.incidents)
