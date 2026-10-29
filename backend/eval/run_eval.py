"""Evaluation harness (spec Phase 7). Needs a loaded graph, embedded documents and an OpenAI key.

    uv run python -m eval.run_eval                       # all 45 questions -> eval/report.md
    uv run python -m eval.run_eval --types cypher --limit 5
    uv run python -m eval.run_eval --compare-cypher-models gpt-a,gpt-b --compare-retrieval

Every end-to-end question costs real tokens; the report states the total.
"""

import argparse
import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from neo4j import Driver

from app.chat.orchestrator import Orchestrator
from app.core.config import Settings, get_settings
from app.core.embeddings import Embedder
from app.core.llm import LLM, TextCompleter, Usage, estimate_cost
from app.models.chat import ChatResponse
from app.retrieval.cypher_guard import run_guarded
from app.retrieval.text2cypher import Text2Cypher
from app.retrieval.vector_rag import KnowledgeBase, SearchMode
from eval.metrics import (
    gold_keywords,
    hit_at_k,
    keyword_recall,
    mean,
    percentile,
    result_contains_gold,
    result_sets_match,
)

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
QUESTIONS_FILE = HERE / "questions.yaml"
REPORT_FILE = HERE / "report.md"
TYPES = ("cypher", "rag", "hybrid")


@dataclass
class QuestionResult:
    id: str
    type: str
    question: str
    expected_route: str
    route: str | None = None
    route_ok: bool = False
    latency_ms: int = 0
    cost_usd: float = 0.0
    answer: str = ""
    error: str | None = None
    generated_cypher: str | None = None
    cypher_ran: bool | None = None
    cypher_match: bool | None = None
    cypher_contains_gold: bool | None = None
    rag_hit: bool | None = None
    retrieved_docs: list[str] = field(default_factory=list)
    keyword_recall: float | None = None
    tools_ok: bool | None = None
    tools: list[str] = field(default_factory=list)


def load_questions(
    path: Path = QUESTIONS_FILE, types: tuple[str, ...] = TYPES, limit: int | None = None
) -> list[dict[str, Any]]:
    questions = yaml.safe_load(path.read_text())
    selected: list[dict[str, Any]] = []
    for t in types:
        of_type = [q for q in questions if q["type"] == t]
        selected += of_type[:limit] if limit else of_type
    return selected


def retrieved_docs(response: ChatResponse) -> list[str]:
    """Document ids returned by the knowledge-base searches, in retrieval order."""
    docs: list[str] = []
    for tool in response.trace.tools:
        if tool.name != "search_knowledge_base":
            continue
        for src in tool.sources:
            if src.doc_id and src.doc_id not in docs:
                docs.append(src.doc_id)
    return docs


def score(q: dict[str, Any], response: ChatResponse, driver: Driver) -> QuestionResult:
    trace = response.trace
    r = QuestionResult(
        id=q["id"],
        type=q["type"],
        question=q["question"],
        expected_route=q["expected_route"],
        route=trace.route,
        route_ok=trace.route == q["expected_route"],
        latency_ms=trace.total_latency_ms,
        cost_usd=trace.cost_usd,
        answer=response.answer,
        tools=[t.name for t in trace.tools],
    )
    keywords = list(q.get("must_mention", []))

    if "gold_cypher" in q:
        gold = run_guarded(driver, q["gold_cypher"])
        call = next((t for t in trace.tools if t.name == "query_twin_data"), None)
        r.generated_cypher = call.cypher if call else None
        r.cypher_ran = bool(call and call.cypher and not call.error)
        if call is not None and call.cypher and r.cypher_ran and gold.error is None:
            got = run_guarded(driver, call.cypher)
            r.cypher_match = got.error is None and result_sets_match(gold.rows, got.rows)
            r.cypher_contains_gold = got.error is None and result_contains_gold(gold.rows, got.rows)
        else:
            r.cypher_match = False
            r.cypher_contains_gold = False
        if q.get("mention_from_gold") and gold.error is None:
            keywords += gold_keywords(gold.rows)

    if "expected_sources_any" in q:
        r.retrieved_docs = retrieved_docs(response)
        r.rag_hit = hit_at_k(q["expected_sources_any"], r.retrieved_docs, 5)

    if "expected_tools" in q:
        r.tools_ok = set(q["expected_tools"]) <= set(r.tools)

    r.keyword_recall = keyword_recall(keywords, response.answer)
    return r


async def evaluate(
    orchestrator: Orchestrator, driver: Driver, questions: list[dict[str, Any]]
) -> list[QuestionResult]:
    results: list[QuestionResult] = []
    for i, q in enumerate(questions, start=1):
        logger.info("[%d/%d] %s %s", i, len(questions), q["id"], q["question"])
        try:
            response = await orchestrator.run_to_completion(f"eval-{q['id']}", q["question"])
            results.append(await asyncio.to_thread(score, q, response, driver))
        except Exception as exc:
            logger.exception("question %s failed", q["id"])
            results.append(
                QuestionResult(
                    q["id"],
                    q["type"],
                    q["question"],
                    q["expected_route"],
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return results


# ---- aggregation ---------------------------------------------------------------------------


def _rate(flags: list[bool]) -> float | None:
    return sum(flags) / len(flags) if flags else None


def summarise(results: list[QuestionResult]) -> dict[str, Any]:
    ok = [r for r in results if r.error is None]
    latencies = [float(r.latency_ms) for r in ok]
    return {
        "questions": len(results),
        "errors": len(results) - len(ok),
        "route_accuracy": _rate([r.route_ok for r in results]),
        "route_accuracy_by_type": {
            t: _rate([r.route_ok for r in results if r.type == t])
            for t in TYPES
            if any(r.type == t for r in results)
        },
        "cypher_execution_rate": _rate(
            [bool(r.cypher_ran) for r in results if r.cypher_ran is not None]
        ),
        "cypher_contains_gold": _rate(
            [bool(r.cypher_contains_gold) for r in results if r.cypher_contains_gold is not None]
        ),
        "cypher_result_accuracy": _rate(
            [bool(r.cypher_match) for r in results if r.cypher_match is not None]
        ),
        "rag_hit_at_5": _rate([bool(r.rag_hit) for r in results if r.rag_hit is not None]),
        "tool_usage_rate": _rate([bool(r.tools_ok) for r in results if r.tools_ok is not None]),
        "keyword_recall": mean([r.keyword_recall for r in results if r.keyword_recall is not None])
        if any(r.keyword_recall is not None for r in results)
        else None,
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
        "avg_cost_usd": mean([r.cost_usd for r in ok]),
        "total_cost_usd": sum(r.cost_usd for r in ok),
    }


# ---- optional comparisons ------------------------------------------------------------------


def compare_cypher_models(
    driver: Driver,
    llm: TextCompleter,
    settings: Settings,
    questions: list[dict[str, Any]],
    models: list[str],
) -> list[dict[str, Any]]:
    """Text2Cypher alone (no orchestrator) for each candidate OPENAI_CYPHER_MODEL."""
    cypher_qs = [q for q in questions if "gold_cypher" in q]
    rows = []
    for model in models:
        t2c = Text2Cypher(driver, llm, settings.model_copy(update={"openai_cypher_model": model}))
        ran, match, usage = [], [], Usage()
        for q in cypher_qs:
            res = t2c.query(q["question"])
            gold = run_guarded(driver, q["gold_cypher"])
            ran.append(res.error is None)
            match.append(
                res.error is None and gold.error is None and result_sets_match(gold.rows, res.rows)
            )
            usage += Usage(res.input_tokens, res.output_tokens)
        rows.append(
            {
                "model": model,
                "execution_rate": _rate(ran),
                "result_accuracy": _rate(match),
                "tokens_in": usage.input_tokens,
                "tokens_out": usage.output_tokens,
                "cost_usd": estimate_cost(usage, settings),
            }
        )
    return rows


def compare_retrieval(
    driver: Driver, embedder: Embedder, settings: Settings, questions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """RAG hit@5, vector-only vs hybrid (vector + fulltext), similarity threshold off."""
    rag_qs = [q for q in questions if "expected_sources_any" in q and q["type"] == "rag"]
    rows = []
    modes: tuple[SearchMode, ...] = ("vector", "hybrid")
    for mode in modes:
        kb = KnowledgeBase(driver, embedder, settings, search_mode=mode)
        hits = []
        for q in rag_qs:
            res = kb.search(q["question"], min_similarity=0.0)
            docs = list(dict.fromkeys(c.doc_id for c in res.chunks))
            hits.append(hit_at_k(q["expected_sources_any"], docs, 5))
        rows.append({"mode": mode, "hit_at_5": _rate(hits), "questions": len(rag_qs)})
    return rows


# ---- report --------------------------------------------------------------------------------


def pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.0f}%"


def render_report(
    results: list[QuestionResult],
    summary: dict[str, Any],
    settings: Settings,
    model_rows: list[dict[str, Any]] | None = None,
    retrieval_rows: list[dict[str, Any]] | None = None,
) -> str:
    lines = [
        "# Evaluation report",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC · {summary['questions']} questions "
        f"({summary['errors']} failed to run) · synthetic data",
        "",
        f"Models: orchestrator `{settings.openai_orchestrator_model}`, "
        f"cypher `{settings.openai_cypher_model}`, "
        f"answer `{settings.openai_answer_model}`, embeddings `{settings.openai_embedding_model}`. "
        f"RAG similarity threshold {settings.rag_min_score}.",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Route accuracy | {pct(summary['route_accuracy'])} |",
    ]
    for t, v in summary["route_accuracy_by_type"].items():
        lines.append(f"| · {t} questions | {pct(v)} |")
    lines += [
        f"| Cypher execution rate | {pct(summary['cypher_execution_rate'])} |",
        f"| Cypher result accuracy, strict (identical result set) | "
        f"{pct(summary['cypher_result_accuracy'])} |",
        f"| Cypher result accuracy, lenient (gold values present) | "
        f"{pct(summary['cypher_contains_gold'])} |",
        f"| RAG hit@5 | {pct(summary['rag_hit_at_5'])} |",
        f"| Expected tools used | {pct(summary['tool_usage_rate'])} |",
        f"| Answer keyword recall | {pct(summary['keyword_recall'])} |",
        f"| Latency p50 / p95 | {summary['latency_p50_ms']:.0f} ms / "
        f"{summary['latency_p95_ms']:.0f} ms |",
        f"| Avg cost per question | ${summary['avg_cost_usd']:.4f} |",
        f"| Total cost | ${summary['total_cost_usd']:.3f} |",
        "",
    ]
    if model_rows:
        lines += [
            "## Cypher model comparison",
            "",
            "| Model | Executes | Result accuracy | Tokens in/out | Cost |",
            "|---|---|---|---|---|",
        ]
        for mrow in model_rows:
            lines.append(
                f"| `{mrow['model']}` | {pct(mrow['execution_rate'])} | "
                f"{pct(mrow['result_accuracy'])} | "
                f"{mrow['tokens_in']}/{mrow['tokens_out']} | ${mrow['cost_usd']:.4f} |"
            )
        lines.append("")
    if retrieval_rows:
        lines += [
            "## Retrieval: vector-only vs hybrid",
            "",
            "| Mode | hit@5 | Questions |",
            "|---|---|---|",
        ]
        for rrow in retrieval_rows:
            lines.append(f"| {rrow['mode']} | {pct(rrow['hit_at_5'])} | {rrow['questions']} |")
        lines.append("")

    problems = [
        r
        for r in results
        if r.error
        or not r.route_ok
        or r.cypher_match is False
        or r.rag_hit is False
        or r.tools_ok is False
        or (r.keyword_recall is not None and r.keyword_recall < 1)
    ]
    lines += ["## Questions that need attention", ""]
    if not problems:
        lines.append("None.")
    else:
        lines += ["| id | question | route (expected) | issues |", "|---|---|---|---|"]
        for r in problems:
            issues = []
            if r.error:
                issues.append(f"error: {r.error[:80]}")
            if not r.route_ok and not r.error:
                issues.append("wrong route")
            if r.cypher_match is False:
                issues.append("cypher result differs" if r.cypher_ran else "cypher failed")
            if r.rag_hit is False:
                issues.append(
                    f"expected source not in top 5 (got {', '.join(r.retrieved_docs) or 'none'})"
                )
            if r.tools_ok is False:
                issues.append(f"tools used: {', '.join(r.tools) or 'none'}")
            if r.keyword_recall is not None and r.keyword_recall < 1:
                issues.append(f"keyword recall {r.keyword_recall:.0%}")
            lines.append(
                f"| {r.id} | {r.question.replace('|', '/')} | {r.route} ({r.expected_route}) | "
                f"{'; '.join(issues)} |"
            )
    lines.append("")
    return "\n".join(lines)


def write_outputs(
    path: Path,
    results: list[QuestionResult],
    summary: dict[str, Any],
    settings: Settings,
    model_rows: list[dict[str, Any]] | None,
    retrieval_rows: list[dict[str, Any]] | None,
) -> None:
    path.write_text(render_report(results, summary, settings, model_rows, retrieval_rows))
    raw = {"summary": summary, "results": [asdict(r) for r in results]}
    path.with_suffix(".json").write_text(json.dumps(raw, indent=2, default=str))


# ---- CLI -----------------------------------------------------------------------------------


async def amain(args: argparse.Namespace) -> None:
    from app.chat.service import build_orchestrator
    from app.core.embeddings import OpenAIEmbedder
    from app.core.neo4j import create_sync_driver

    settings = get_settings()
    types = tuple(t for t in args.types.split(",") if t)
    questions = load_questions(Path(args.questions), types, args.limit)
    driver = create_sync_driver()
    try:
        results: list[QuestionResult] = []
        if not args.skip_e2e:
            results = await evaluate(build_orchestrator(settings), driver, questions)
        model_rows = (
            compare_cypher_models(
                driver, LLM(settings), settings, questions, args.compare_cypher_models.split(",")
            )
            if args.compare_cypher_models
            else None
        )
        retrieval_rows = (
            compare_retrieval(driver, OpenAIEmbedder(settings), settings, questions)
            if args.compare_retrieval
            else None
        )
        summary = summarise(results)
        await asyncio.to_thread(
            write_outputs, Path(args.report), results, summary, settings, model_rows, retrieval_rows
        )
        print(
            f"wrote {args.report}  (route accuracy {pct(summary['route_accuracy'])}, "
            f"total ${summary['total_cost_usd']:.3f})"
        )
    finally:
        driver.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--types", default=",".join(TYPES))
    p.add_argument("--limit", type=int, help="questions per type")
    p.add_argument("--questions", default=str(QUESTIONS_FILE))
    p.add_argument("--report", default=str(REPORT_FILE))
    p.add_argument("--skip-e2e", action="store_true", help="only run the comparisons")
    p.add_argument("--compare-cypher-models", help="comma-separated model names")
    p.add_argument("--compare-retrieval", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
