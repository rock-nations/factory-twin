from pathlib import Path
from typing import Any

import pytest
from neo4j import Driver

from app.chat.memory import SessionStore
from app.chat.orchestrator import Orchestrator
from app.chat.tools import ToolBox
from app.core.config import Settings
from app.ingestion.build_topology import build_topology
from app.ingestion.embed_documents import embed_documents
from app.ingestion.embed_failures import embed_failures
from app.ingestion.init_schema import init_schema
from app.ingestion.load_events import load_events
from app.retrieval.graphrag import IncidentFinder
from app.retrieval.text2cypher import Text2Cypher
from app.retrieval.vector_rag import KnowledgeBase
from eval.metrics import (
    gold_keywords,
    hit_at_k,
    keyword_recall,
    percentile,
    result_sets_match,
    rows_as_set,
)
from eval.run_eval import (
    QUESTIONS_FILE,
    compare_cypher_models,
    compare_retrieval,
    evaluate,
    load_questions,
    render_report,
    summarise,
    write_outputs,
)
from tests.chat_fakes import ScriptedModel
from tests.fakes import HashingEmbedder
from tests.test_embed_integration import DOCS
from tests.test_retrieval_integration import ScriptedLLM


class TestMetrics:
    def test_result_sets_ignore_order_aliases_and_float_noise(self) -> None:
        gold = [{"line": "A1", "n": 13}, {"line": "A2", "n": 13}]
        got = [{"x": 13.0, "y": "a2"}, {"y": "A1", "x": 13}]
        assert result_sets_match(gold, got)

    def test_different_values_do_not_match(self) -> None:
        assert not result_sets_match([{"n": 13}], [{"n": 12}])
        assert not result_sets_match([{"n": 1}, {"n": 2}], [{"n": 1}])

    def test_averages_are_compared_to_one_decimal(self) -> None:
        assert result_sets_match([{"avg": 10.04}], [{"avg": 10.0}])
        assert not result_sets_match([{"avg": 10.0}], [{"avg": 10.5}])

    def test_lists_and_none(self) -> None:
        assert rows_as_set([{"a": ["b", "a"], "c": None}]) == rows_as_set(
            [{"c": None, "a": ["a", "b"]}]
        )

    def test_hit_at_k(self) -> None:
        docs = ["d1", "d2", "d3", "d4", "d5", "d6"]
        assert hit_at_k(["d5"], docs, 5)
        assert not hit_at_k(["d6"], docs, 5)
        assert hit_at_k(["zzz", "d2"], docs, 5)
        assert not hit_at_k([], docs, 5)

    def test_keyword_recall(self) -> None:
        assert keyword_recall(["Bearing", "days"], "Replace the bearing every 180 DAYS") == 1.0
        assert keyword_recall(["bearing", "pump"], "the bearing") == 0.5
        assert keyword_recall([], "anything") is None

    def test_percentile_nearest_rank(self) -> None:
        values = list(map(float, range(1, 101)))
        assert percentile(values, 50) == 50 and percentile(values, 95) == 95
        assert percentile([], 95) == 0.0 and percentile([7.0], 99) == 7.0

    def test_gold_keywords_take_first_column_of_first_rows(self) -> None:
        rows: list[dict[str, Any]] = [
            {"m": "M001", "n": 3},
            {"m": "M002", "n": 4},
            {"m": "M003", "n": 5},
            {"m": "M004"},
        ]
        assert gold_keywords(rows) == ["M001", "M002", "M003"]
        assert gold_keywords([{"avg": 10.5}]) == ["10.5"]
        assert gold_keywords([{"flag": True}]) == []


class TestQuestionSet:
    def test_45_questions_15_per_type_with_unique_ids(self) -> None:
        qs = load_questions(QUESTIONS_FILE)
        assert len(qs) == 45 and len({q["id"] for q in qs}) == 45
        for t in ("cypher", "rag", "hybrid"):
            assert sum(1 for q in qs if q["type"] == t) == 15
        assert all(q["expected_route"] == q["type"] for q in qs)

    def test_each_type_has_what_it_is_scored_on(self) -> None:
        for q in load_questions(QUESTIONS_FILE):
            if q["type"] == "cypher":
                assert q["gold_cypher"].strip().upper().startswith("MATCH") and q.get(
                    "mention_from_gold"
                )
            if q["type"] == "rag":
                assert q["expected_sources_any"] and q["must_mention"]
            if q["type"] == "hybrid":
                assert q["expected_tools"] and q["must_mention"]

    def test_limit_applies_per_type(self) -> None:
        assert len(load_questions(QUESTIONS_FILE, limit=2)) == 6
        assert [q["type"] for q in load_questions(QUESTIONS_FILE, ("rag",), 3)] == ["rag"] * 3

    def test_expected_documents_are_ones_the_generator_produces(self) -> None:
        valid = {f"manual-model{i}" for i in range(1, 5)} | {f"sop-comp{i}" for i in range(1, 5)}
        valid |= {f"troubleshooting-error{i}" for i in range(1, 6)}
        for q in load_questions(QUESTIONS_FILE):
            assert set(q.get("expected_sources_any", [])) <= valid, q["id"]

    @pytest.mark.integration
    def test_every_gold_query_passes_the_guard_and_runs(
        self, clean_db: Driver, test_settings: Settings
    ) -> None:
        from app.retrieval.cypher_guard import run_guarded

        init_schema(clean_db, test_settings)
        for q in load_questions(QUESTIONS_FILE, ("cypher",)):
            res = run_guarded(clean_db, q["gold_cypher"])
            assert res.error is None, f"{q['id']}: {res.error}"


SETTINGS = Settings(
    _env_file=None, openai_cypher_model="cypher-model", rag_min_score=0.5,
    price_input_per_m=1.0, price_output_per_m=1.0,
)  # fmt: skip


@pytest.fixture
def world(clean_db: Driver, pdm_dir: Path, test_settings: Settings, tmp_path: Path) -> Driver:
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
    return clean_db


def orchestrator(world: Driver, model: ScriptedModel, cypher_reply: str) -> Orchestrator:
    emb = HashingEmbedder()
    toolbox = ToolBox(
        world,
        Text2Cypher(world, ScriptedLLM(cypher_reply), SETTINGS),
        KnowledgeBase(world, emb, SETTINGS),
        IncidentFinder(world, emb),
        "cypher-model",
    )
    return Orchestrator(model, toolbox, SessionStore(), SETTINGS)


@pytest.mark.integration
class TestHarness:
    async def test_scores_a_correct_cypher_answer(self, world: Driver) -> None:
        q = {
            "id": "t1", "type": "cypher", "expected_route": "cypher", "question": "How many plants?",
            "gold_cypher": "MATCH (p:Plant) RETURN count(p) AS n", "mention_from_gold": True,
        }  # fmt: skip
        model = ScriptedModel(
            [[("query_twin_data", '{"question": "How many plants?"}')], []],
            answer="There are 2 plants.",
        )
        orch = orchestrator(world, model, "MATCH (x:Plant) RETURN count(x) AS plants")
        (r,) = await evaluate(orch, world, [q])
        assert r.error is None and r.route == "cypher" and r.route_ok
        assert r.cypher_ran and r.cypher_match  # different alias, same result set
        assert r.keyword_recall == 1.0  # answer mentions "2"

    async def test_flags_a_wrong_query_and_missing_keyword(self, world: Driver) -> None:
        q = {
            "id": "t2", "type": "cypher", "expected_route": "cypher", "question": "How many lines?",
            "gold_cypher": "MATCH (l:ProductionLine) RETURN count(l) AS n", "mention_from_gold": True,
        }  # fmt: skip
        model = ScriptedModel([[("query_twin_data", '{"question": "x"}')], []], answer="Unknown.")
        orch = orchestrator(world, model, "MATCH (p:Plant) RETURN count(p) AS n")
        (r,) = await evaluate(orch, world, [q])
        assert r.cypher_ran is True and r.cypher_match is False and r.keyword_recall == 0.0

    async def test_scores_rag_hit_and_tool_usage(self, world: Driver) -> None:
        q = {
            "id": "t3", "type": "rag", "expected_route": "rag", "question": "replace spindle bearing",
            "expected_sources_any": ["sop-comp1"], "expected_tools": ["search_knowledge_base"],
            "must_mention": ["bearing"],
        }  # fmt: skip
        model = ScriptedModel(
            [[("search_knowledge_base", '{"query": "replace the worn spindle bearing"}')], []],
            answer="Press out the bearing [1].",
        )
        (r,) = await evaluate(orchestrator(world, model, "NO_QUERY"), world, [q])
        assert r.rag_hit is True and r.retrieved_docs[0] == "sop-comp1"
        assert r.tools_ok is True and r.keyword_recall == 1.0

    async def test_wrong_route_and_errors_are_recorded_not_raised(self, world: Driver) -> None:
        qs = [
            {"id": "a", "type": "rag", "expected_route": "rag", "question": "hello"},
            {"id": "b", "type": "rag", "expected_route": "rag", "question": "boom"},
        ]
        good = orchestrator(world, ScriptedModel([[]]), "NO_QUERY")
        res = await evaluate(good, world, qs[:1])
        assert res[0].route == "direct" and not res[0].route_ok
        bad = orchestrator(world, ScriptedModel([], fail_on_decide=True), "NO_QUERY")
        (err,) = await evaluate(bad, world, qs[1:])
        assert err.error and "model unavailable" in err.error and not err.route_ok

    async def test_report_has_summary_and_problem_table(
        self, world: Driver, tmp_path: Path
    ) -> None:
        qs = [
            {"id": "ok", "type": "rag", "expected_route": "rag", "question": "replace spindle bearing",
             "expected_sources_any": ["sop-comp1"], "must_mention": ["bearing"]},
            {"id": "bad", "type": "hybrid", "expected_route": "hybrid", "question": "hi | there",
             "expected_tools": ["get_machine_context"], "must_mention": ["M017"]},
        ]  # fmt: skip
        model = ScriptedModel(
            [[("search_knowledge_base", '{"query": "spindle bearing replace"}')], [], []],
            answer="the bearing",
        )
        results = await evaluate(orchestrator(world, model, "NO_QUERY"), world, qs)
        summary = summarise(results)
        assert summary["questions"] == 2 and summary["route_accuracy"] == 0.5
        assert summary["rag_hit_at_5"] == 1.0 and summary["tool_usage_rate"] == 0.0
        assert summary["latency_p95_ms"] >= summary["latency_p50_ms"] >= 0
        assert summary["total_cost_usd"] > 0
        out = tmp_path / "report.md"
        write_outputs(out, results, summary, SETTINGS, None, None)
        text = out.read_text()
        assert "| Route accuracy | 50% |" in text and "| RAG hit@5 | 100% |" in text
        assert "## Questions that need attention" in text
        assert (
            "| bad | hi / there |" in text and "wrong route" in text and "tools used: none" in text
        )
        assert "| ok |" not in text.split("need attention")[1]
        assert out.with_suffix(".json").exists()

    def test_cypher_model_comparison_separates_good_from_bad(self, world: Driver) -> None:
        qs = [
            {
                "id": "c", "type": "cypher", "expected_route": "cypher", "question": "How many plants?",
                "gold_cypher": "MATCH (p:Plant) RETURN count(p) AS n",
            }
        ]  # fmt: skip
        # model-a answers correctly; model-b writes a query for the wrong thing
        llm = ScriptedLLM(
            "MATCH (p:Plant) RETURN count(p) AS n", "MATCH (l:ProductionLine) RETURN count(l) AS n"
        )
        rows = compare_cypher_models(world, llm, SETTINGS, qs, ["model-a", "model-b"])
        by_model = {r["model"]: r for r in rows}
        assert by_model["model-a"]["result_accuracy"] == 1.0
        assert (
            by_model["model-b"]["execution_rate"] == 1.0
            and by_model["model-b"]["result_accuracy"] == 0.0
        )
        assert [c["model"] for c in llm.calls] == ["model-a", "model-b"]

    def test_retrieval_comparison_hybrid_vs_vector(self, world: Driver) -> None:
        qs = [{"id": "r", "type": "rag", "expected_route": "rag", "question": "replace spindle bearing",
               "expected_sources_any": ["sop-comp1"]}]  # fmt: skip
        rows = compare_retrieval(world, HashingEmbedder(), SETTINGS, qs)
        assert {r["mode"] for r in rows} == {"vector", "hybrid"}
        assert all(r["hit_at_5"] == 1.0 for r in rows)

    def test_render_report_without_results(self) -> None:
        text = render_report([], summarise([]), SETTINGS)
        assert "# Evaluation report" in text and "None." in text


def test_summarise_handles_empty() -> None:
    s: dict[str, Any] = summarise([])
    assert s["questions"] == 0 and s["route_accuracy"] is None and s["avg_cost_usd"] == 0.0


class TestLenientMetric:
    def test_extra_columns_and_rows_are_accepted(self) -> None:
        from eval.metrics import result_contains_gold

        assert result_contains_gold([{"oldest": 20}], [{"machine": "M007", "age": 20}])
        assert result_contains_gold(
            [{"t": "Mira", "n": 89}], [{"x": "Mira", "y": 89}, {"x": "Lukas", "y": 87}]
        )

    def test_missing_values_are_rejected(self) -> None:
        from eval.metrics import result_contains_gold

        assert not result_contains_gold([{"n": 52}], [{"n": 48}])
        assert not result_contains_gold([{"p": "A", "n": 1}], [{"p": "A", "n": 2}])
        assert not result_contains_gold([{"n": 1}], [])

    def test_strict_metric_is_still_stricter(self) -> None:
        got = [{"machine": "M007", "age": 20}]
        assert not result_sets_match([{"oldest": 20}], got)
