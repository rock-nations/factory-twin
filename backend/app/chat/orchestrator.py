"""Agent loop: the orchestrator model picks tools (max 4 rounds), another call writes the answer."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.chat.memory import SessionStore, Turn
from app.chat.model import ChatModel
from app.chat.tools import TOOL_FAMILY, TOOL_SPECS, SourceRegistry, ToolOutcome, ToolRunner
from app.core.config import Settings, get_settings
from app.core.llm import Usage, estimate_cost
from app.models.chat import (
    ChatEvent,
    ChatResponse,
    DoneEvent,
    ErrorEvent,
    LLMCall,
    Route,
    RouteEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    Trace,
)

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
MAX_TOOL_ROUNDS = 4
ORCHESTRATOR_RESULT_CHARS = 6_000  # what the planner sees of each tool result
ANSWER_RESULT_CHARS = 20_000  # what the answer writer sees of each tool result


def _prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text().strip()


def route_for(tool_names: set[str]) -> Route:
    families = {TOOL_FAMILY[n] for n in tool_names if n in TOOL_FAMILY}
    if not families:
        return "direct"
    if len(families) > 1:
        return "hybrid"
    return "cypher" if "cypher" in families else "rag"


def compact_json(payload: dict[str, Any], limit: int) -> str:
    text = json.dumps(payload, default=str, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + f"… [truncated, {len(text)} chars]"


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


class Orchestrator:
    def __init__(
        self,
        model: ChatModel,
        tools: ToolRunner,
        store: SessionStore | None = None,
        settings: Settings | None = None,
        max_rounds: int = MAX_TOOL_ROUNDS,
    ) -> None:
        self.model = model
        self.tools = tools
        self.store = store or SessionStore()
        self.settings = settings or get_settings()
        self.max_rounds = max_rounds
        # static prefixes: identical on every request so OpenAI prompt caching can kick in
        self.orchestrator_prompt = _prompt("orchestrator_system.md")
        self.answer_prompt = _prompt("answer_system.md")

    def _cost(self, usage: Usage) -> float:
        return estimate_cost(usage, self.settings)

    async def run(self, session_id: str, message: str) -> AsyncIterator[ChatEvent]:
        try:
            async for event in self._run(session_id, message):
                yield event
        except Exception as exc:
            logger.exception("chat request failed")
            yield ErrorEvent(message=f"{type(exc).__name__}: {exc}")

    async def run_to_completion(self, session_id: str, message: str) -> ChatResponse:
        async for event in self.run(session_id, message):
            if isinstance(event, DoneEvent):
                return ChatResponse(answer=event.answer, trace=event.trace)
            if isinstance(event, ErrorEvent):
                raise RuntimeError(event.message)
        raise RuntimeError("chat produced no answer")

    async def _run(self, session_id: str, message: str) -> AsyncIterator[ChatEvent]:
        started = time.perf_counter()
        session = self.store.get(session_id)
        session.remember_entities(message)
        trace = Trace()
        sources = SourceRegistry()
        used_tools: set[str] = set()
        results: list[tuple[str, dict[str, Any], ToolOutcome]] = []
        step_ms = {"orchestrate": 0, "tools": 0, "answer": 0}

        hint = session.entity_hint()
        user_content = f"{message}\n\n[{hint}]" if hint else message
        input_items: list[dict[str, Any]] = [
            *session.history_messages(),
            {"role": "user", "content": user_content},
        ]
        previous_id: str | None = None
        sent_route: Route | None = None

        for round_no in range(1, self.max_rounds + 1):
            decision = await self.model.decide(
                self.orchestrator_prompt, input_items, TOOL_SPECS, previous_id
            )
            usage = decision.usage
            trace.llm_calls.append(
                LLMCall(
                    step="orchestrate",
                    model=self.model.orchestrator_model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cached_tokens=decision.cached_tokens,
                    cost_usd=self._cost(usage),
                    latency_ms=decision.latency_ms,
                )
            )
            step_ms["orchestrate"] += decision.latency_ms
            if not decision.tool_calls:
                break
            trace.rounds = round_no

            used_tools |= {c.name for c in decision.tool_calls}
            route = route_for(used_tools)
            if route != sent_route:
                sent_route = route
                yield RouteEvent(route=route)

            parsed: list[tuple[str, str, dict[str, Any] | None]] = []
            for call in decision.tool_calls:
                args = _parse_args(call.arguments)
                parsed.append((call.call_id, call.name, args))
                yield ToolCallEvent(name=call.name, args=args or {})

            tools_start = time.perf_counter()
            outcomes = await asyncio.gather(
                *(
                    self.tools.run(name, args, sources) if args is not None else _bad_args(name)
                    for _, name, args in parsed
                )
            )
            step_ms["tools"] += _ms(tools_start)

            next_items: list[dict[str, Any]] = []
            for (call_id, name, args), outcome in zip(parsed, outcomes, strict=True):
                results.append((name, args or {}, outcome))
                trace.tools.append(outcome.trace)
                if outcome.nested_llm and outcome.nested_llm[1]:
                    model_name, tin, tout = outcome.nested_llm
                    trace.llm_calls.append(
                        LLMCall(
                            step="text2cypher",
                            model=model_name,
                            input_tokens=tin,
                            output_tokens=tout,
                            cost_usd=self._cost(Usage(tin, tout)),
                            latency_ms=outcome.trace.latency_ms,
                        )
                    )
                session.remember_entities(outcome.entities_text, json.dumps(args or {}))
                yield ToolResultEvent(
                    name=name,
                    summary=outcome.trace.summary,
                    cypher=outcome.trace.cypher,
                    row_count=outcome.trace.row_count,
                    sources=outcome.trace.sources,
                )
                next_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": compact_json(outcome.payload, ORCHESTRATOR_RESULT_CHARS),
                    }
                )
            input_items = next_items
            previous_id = decision.response_id

        trace.route = route_for(used_tools)
        if sent_route is None:
            yield RouteEvent(route=trace.route)

        # ---- final answer, streamed -----------------------------------------------------------
        answer_messages = [
            *session.history_messages(),
            {"role": "user", "content": self._answer_input(message, hint, results, sources)},
        ]
        answer_start = time.perf_counter()
        parts: list[str] = []
        answer_usage = Usage()
        cached = 0
        async for chunk in self.model.stream_answer(self.answer_prompt, answer_messages):
            if chunk.delta:
                parts.append(chunk.delta)
                yield TokenEvent(delta=chunk.delta)
            if chunk.usage:
                answer_usage, cached = chunk.usage, chunk.cached_tokens
        step_ms["answer"] = _ms(answer_start)
        trace.llm_calls.append(
            LLMCall(
                step="answer",
                model=self.model.answer_model,
                input_tokens=answer_usage.input_tokens,
                output_tokens=answer_usage.output_tokens,
                cached_tokens=cached,
                cost_usd=self._cost(answer_usage),
                latency_ms=step_ms["answer"],
            )
        )

        answer = "".join(parts).strip()
        session.turns.append(Turn(message, answer))
        session.remember_entities(answer)

        trace.step_latency_ms = step_ms
        trace.total_latency_ms = _ms(started)
        trace.input_tokens = sum(c.input_tokens for c in trace.llm_calls)
        trace.output_tokens = sum(c.output_tokens for c in trace.llm_calls)
        trace.cost_usd = round(sum(c.cost_usd for c in trace.llm_calls), 6)
        logger.info(
            "chat route=%s tools=%s latency=%dms tokens=%d/%d cost=$%.5f",
            trace.route,
            [t.name for t in trace.tools],
            trace.total_latency_ms,
            trace.input_tokens,
            trace.output_tokens,
            trace.cost_usd,
        )
        yield DoneEvent(answer=answer, trace=trace)

    def _answer_input(
        self,
        message: str,
        hint: str,
        results: list[tuple[str, dict[str, Any], ToolOutcome]],
        sources: SourceRegistry,
    ) -> str:
        head = f"Question: {message}" + (f"\n{hint}" if hint else "")
        if not results:
            return f"{head}\n\nNo tools were used and no data was retrieved."
        blocks = [
            f"### Result {i}: {name}({json.dumps(args, ensure_ascii=False)})\n"
            f"{compact_json(outcome.payload, ANSWER_RESULT_CHARS)}"
            for i, (name, args, outcome) in enumerate(results, start=1)
        ]
        refs = "\n".join(
            f"[{s.ref}] {s.title}" + (f" ({s.doc_type})" if s.doc_type else f" ({s.kind})")
            for s in sources.all()
        )
        return f"{head}\n\nTool results:\n\n" + "\n\n".join(blocks) + f"\n\nSources:\n{refs}"


def _parse_args(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


async def _bad_args(name: str) -> ToolOutcome:
    from app.chat.tools import error_outcome

    return error_outcome(name, {}, "tool arguments were not valid JSON")
