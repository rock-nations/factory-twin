"""The four orchestrator tools: schemas, execution, source numbering and LLM-facing payloads."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from neo4j import Driver

from app.graph import constants as C
from app.models.chat import SourceRef, ToolTrace
from app.models.retrieval import RagResult, SimilarIncidentsResult, Text2CypherResult
from app.retrieval.graphrag import IncidentFinder
from app.retrieval.machine_context import get_machine_context
from app.retrieval.text2cypher import Text2Cypher
from app.retrieval.vector_rag import KnowledgeBase

Family = Literal["cypher", "rag"]

TOOL_FAMILY: dict[str, Family] = {
    "query_twin_data": "cypher",
    "get_machine_context": "cypher",
    "search_knowledge_base": "rag",
    "find_similar_incidents": "rag",
}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "query_twin_data",
        "description": (
            "Answer a factual question from the factory graph by generating and running a "
            "read-only Cypher query: counts, lists, rankings, live state, alarms, maintenance and "
            "failure history, line/plant structure, upstream/downstream relationships."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Self-contained natural-language question (no pronouns).",
                }
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_machine_context",
        "description": (
            "Get the full current picture of ONE machine: latest sensor values vs thresholds, open "
            "alarms, components and last replacement, last 5 maintenance events, and upstream/"
            "downstream machines (2 hops)."
        ),
        "parameters": {
            "type": "object",
            "properties": {"machine_id": {"type": "string", "description": "e.g. M017"}},
            "required": ["machine_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "search_knowledge_base",
        "description": (
            "Search manuals, SOPs, troubleshooting guides and incident reports (hybrid vector + "
            "keyword search). Use for procedures, specs, normal ranges, error-code meanings, "
            "safety."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "doc_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": C.DOC_TYPES},
                    "description": "Restrict to these document types.",
                },
                "machine_model": {"type": "string", "enum": C.MODELS},
                "component": {
                    "type": "string",
                    "enum": list(C.COMPONENTS),
                    "description": "comp1 Spindle bearing, comp2 Hydraulic pump, "
                    "comp3 Drive motor, comp4 Coolant valve",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "find_similar_incidents",
        "description": (
            "Find past failures similar to a described symptom or situation, with the machine, "
            "failed component, how it was resolved and the incident report."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Symptoms, e.g. 'rising vibration on a model3 machine'.",
                },
                "machine_model": {"type": "string", "enum": C.MODELS},
            },
            "required": ["description"],
            "additionalProperties": False,
        },
    },
]


class SourceRegistry:
    """Numbers sources [1], [2], ... for one request so the answer can cite them."""

    def __init__(self) -> None:
        self._by_key: dict[str, SourceRef] = {}

    def register(
        self,
        key: str,
        kind: Literal["cypher", "document", "incident", "machine"],
        title: str,
        doc_id: str | None = None,
        doc_type: str | None = None,
    ) -> SourceRef:
        if key not in self._by_key:
            self._by_key[key] = SourceRef(
                ref=len(self._by_key) + 1, kind=kind, title=title, doc_id=doc_id, doc_type=doc_type
            )
        return self._by_key[key]

    def all(self) -> list[SourceRef]:
        return list(self._by_key.values())


@dataclass
class ToolOutcome:
    payload: dict[str, Any]  # what the LLMs see
    trace: ToolTrace
    nested_llm: tuple[str, int, int] | None = None  # (model, input_tokens, output_tokens)
    entities_text: str = ""


class ToolRunner(Protocol):
    async def run(
        self, name: str, args: dict[str, Any], sources: SourceRegistry
    ) -> ToolOutcome: ...


@dataclass
class ToolBox:
    driver: Driver
    text2cypher: Text2Cypher
    knowledge_base: KnowledgeBase
    incidents: IncidentFinder
    cypher_model: str = ""
    _dispatch: dict[str, Any] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._dispatch = {
            "query_twin_data": self._query_twin_data,
            "get_machine_context": self._machine_context,
            "search_knowledge_base": self._search_kb,
            "find_similar_incidents": self._similar,
        }

    async def run(self, name: str, args: dict[str, Any], sources: SourceRegistry) -> ToolOutcome:
        start = time.perf_counter()
        fn = self._dispatch.get(name)
        if fn is None:
            outcome = error_outcome(name, args, f"unknown tool '{name}'")
        else:
            try:
                # retrieval is synchronous (neo4j-graphrag); keep the event loop free
                outcome = await asyncio.to_thread(fn, args, sources)
            except Exception as exc:  # tool failures must not kill the conversation
                outcome = error_outcome(name, args, f"{type(exc).__name__}: {exc}")
        outcome.trace.latency_ms = int((time.perf_counter() - start) * 1000)
        return outcome

    # ---- implementations (sync, run in a worker thread) -------------------------------------

    def _query_twin_data(self, args: dict[str, Any], sources: SourceRegistry) -> ToolOutcome:
        question = str(args.get("question", ""))
        res: Text2CypherResult = self.text2cypher.query(question)
        src = sources.register(f"cypher:{res.cypher or question}", "cypher", "Graph query result")
        payload: dict[str, Any] = {
            "ref": src.ref,
            "cypher": res.cypher,
            "row_count": res.row_count,
            "truncated": res.truncated,
            "rows": res.rows,
        }
        if res.error:
            payload["error"] = res.error
            summary = f"query failed: {res.error[:160]}"
        elif res.row_count == 0:
            payload["note"] = "The query ran successfully but returned no rows."
            summary = "no rows"
        else:
            summary = f"{res.row_count} row(s)" + (" (truncated)" if res.truncated else "")
        trace = ToolTrace(
            name="query_twin_data",
            arguments=args,
            summary=summary,
            cypher=res.cypher or None,
            row_count=res.row_count,
            truncated=res.truncated,
            preview_rows=res.rows[:10],
            sources=[src] if not res.error else [],
            error=res.error,
        )
        return ToolOutcome(
            payload,
            trace,
            nested_llm=(self.cypher_model, res.input_tokens, res.output_tokens),
            entities_text=json.dumps(res.rows, default=str)[:2000],
        )

    def _machine_context(self, args: dict[str, Any], sources: SourceRegistry) -> ToolOutcome:
        ctx = get_machine_context(self.driver, str(args.get("machine_id", "")))
        if "error" in ctx:
            return error_outcome("get_machine_context", args, ctx["error"])
        machine = ctx["machine"]["id"]
        src = sources.register(f"machine:{machine}", "machine", f"Live context of {machine}")
        ctx = {"ref": src.ref, **ctx}
        alarms = len(ctx["open_alarms"])
        trace = ToolTrace(
            name="get_machine_context",
            arguments=args,
            summary=f"{machine}: {alarms} open alarm(s), {len(ctx['downstream'])} downstream",
            sources=[src],
        )
        return ToolOutcome(ctx, trace, entities_text=machine)

    def _search_kb(self, args: dict[str, Any], sources: SourceRegistry) -> ToolOutcome:
        res: RagResult = self.knowledge_base.search(
            str(args.get("query", "")),
            doc_types=args.get("doc_types"),
            machine_model=args.get("machine_model"),
            component=args.get("component"),
        )
        refs = {
            s.doc_id: sources.register(
                f"doc:{s.doc_id}", "document", s.title, doc_id=s.doc_id, doc_type=s.doc_type
            )
            for s in res.sources
        }
        payload: dict[str, Any] = {
            "no_relevant_documents": res.no_relevant_documents,
            "message": res.message,
            "chunks": [
                {
                    "ref": refs[c.doc_id].ref,
                    "document": c.title,
                    "doc_type": c.doc_type,
                    "similarity": round(c.similarity, 3),
                    "text": c.text,
                    "context_before": _clip(c.previous),
                    "context_after": _clip(c.next),
                    "about": c.about,
                }
                for c in res.chunks
            ],
        }
        trace = ToolTrace(
            name="search_knowledge_base",
            arguments=args,
            summary=res.message
            or f"{len(res.chunks)} chunk(s) from {len(res.sources)} document(s)",
            sources=list(refs.values()),
            retrieval_scores=[round(c.similarity, 3) for c in res.chunks],
        )
        return ToolOutcome(payload, trace)

    def _similar(self, args: dict[str, Any], sources: SourceRegistry) -> ToolOutcome:
        res: SimilarIncidentsResult = self.incidents.find(
            str(args.get("description", "")), args.get("machine_model")
        )
        incidents = []
        refs: list[SourceRef] = []
        for i in res.incidents:
            src = sources.register(
                f"incident:{i.failure_id}",
                "incident",
                i.report_title or f"Incident {i.failure_id}",
                doc_id=i.report_id,
            )
            refs.append(src)
            incidents.append({"ref": src.ref, **i.model_dump(exclude_none=True)})
        payload: dict[str, Any] = {"incidents": incidents, "message": res.message}
        trace = ToolTrace(
            name="find_similar_incidents",
            arguments=args,
            summary=res.message or f"{len(incidents)} similar incident(s)",
            sources=refs,
            retrieval_scores=[round(i.similarity, 3) for i in res.incidents],
        )
        return ToolOutcome(payload, trace)


def _clip(text: str | None, n: int = 600) -> str | None:
    return None if text is None else text[:n]


def error_outcome(name: str, args: dict[str, Any], message: str) -> ToolOutcome:
    return ToolOutcome(
        {"error": message},
        ToolTrace(name=name, arguments=args, summary=f"error: {message}", error=message),
    )
