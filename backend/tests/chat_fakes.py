from collections.abc import AsyncIterator
from typing import Any

from app.chat.model import AnswerChunk, Decision, ToolCall
from app.chat.tools import SourceRegistry, ToolOutcome
from app.core.llm import Usage
from app.models.chat import ToolTrace


class ScriptedModel:
    """Each round is a list of (tool_name, arguments_json); an empty list means 'no more tools'."""

    orchestrator_model = "orch-model"
    answer_model = "answer-model"

    def __init__(
        self,
        rounds: list[list[tuple[str, str]]],
        answer: str = "Done [1].",
        fail_on_decide: bool = False,
    ) -> None:
        self.rounds = list(rounds)
        self.answer = answer
        self.fail_on_decide = fail_on_decide
        self.decide_inputs: list[dict[str, Any]] = []
        self.answer_inputs: list[list[dict[str, str]]] = []

    async def decide(
        self,
        system: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None,
    ) -> Decision:
        if self.fail_on_decide:
            raise RuntimeError("model unavailable")
        self.decide_inputs.append({"input": input_items, "previous": previous_response_id})
        n = len(self.decide_inputs)
        calls = self.rounds.pop(0) if self.rounds else []
        return Decision(
            tool_calls=[
                ToolCall(f"call_{n}_{i}", name, args) for i, (name, args) in enumerate(calls)
            ],
            response_id=f"resp_{n}",
            usage=Usage(100, 20),
            cached_tokens=40,
            latency_ms=5,
        )

    async def stream_answer(
        self, system: str, messages: list[dict[str, str]]
    ) -> AsyncIterator[AnswerChunk]:
        self.answer_inputs.append(messages)
        for word in self.answer.split(" "):
            yield AnswerChunk(delta=word + " ")
        yield AnswerChunk(usage=Usage(300, 50), cached_tokens=100)


class FakeTools:
    def __init__(self, fail: set[str] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail or set()

    async def run(self, name: str, args: dict[str, Any], sources: SourceRegistry) -> ToolOutcome:
        self.calls.append((name, args))
        if name in self.fail:
            return ToolOutcome(
                {"error": "boom"},
                ToolTrace(name=name, arguments=args, summary="error: boom", error="boom"),
            )
        src = sources.register(
            f"{name}:{args}",
            "cypher" if name == "query_twin_data" else "document",
            f"{name} result",
        )
        trace = ToolTrace(
            name=name,
            arguments=args,
            summary="1 row",
            cypher="MATCH (m) RETURN m" if name == "query_twin_data" else None,
            row_count=1 if name == "query_twin_data" else None,
            sources=[src],
            latency_ms=7,
        )
        nested = ("cypher-model", 50, 10) if name == "query_twin_data" else None
        return ToolOutcome(
            {"ref": src.ref, "rows": [{"machine": "M017"}]},
            trace,
            nested_llm=nested,
            entities_text="M017",
        )
