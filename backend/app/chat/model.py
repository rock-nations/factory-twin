"""LLM access for the orchestrator, behind a small protocol so tests can script it."""

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol, cast

from openai import AsyncOpenAI, AsyncStream
from openai.types.responses import (
    ResponseCompletedEvent,
    ResponseStreamEvent,
    ResponseTextDeltaEvent,
)

from app.core.config import Settings
from app.core.llm import Usage, reasoning_kwargs, require_setting


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: str  # raw JSON text from the model


@dataclass
class Decision:
    tool_calls: list[ToolCall]
    response_id: str
    usage: Usage
    cached_tokens: int = 0
    latency_ms: int = 0
    text: str = ""


@dataclass
class AnswerChunk:
    delta: str = ""
    usage: Usage | None = None
    cached_tokens: int = 0


class ChatModel(Protocol):
    orchestrator_model: str
    answer_model: str

    async def decide(
        self,
        system: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None,
    ) -> Decision: ...

    def stream_answer(
        self, system: str, messages: list[dict[str, str]]
    ) -> AsyncIterator[AnswerChunk]: ...


class OpenAIChatModel:
    """Responses API: tool calling for planning, streaming for the final answer."""

    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None) -> None:
        self.orchestrator_model = require_setting(
            settings.openai_orchestrator_model, "OPENAI_ORCHESTRATOR_MODEL"
        )
        self.answer_model = require_setting(settings.openai_answer_model, "OPENAI_ANSWER_MODEL")
        self.reasoning = reasoning_kwargs(settings)
        self.client = client or AsyncOpenAI(
            api_key=require_setting(settings.openai_api_key, "OPENAI_API_KEY")
        )

    async def decide(
        self,
        system: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None,
    ) -> Decision:
        start = time.perf_counter()
        # previous_response_id keeps reasoning/tool items server-side between rounds
        kwargs: dict[str, Any] = {
            "model": self.orchestrator_model,
            "instructions": system,
            "input": input_items,
            "tools": tools,
            "tool_choice": "auto",
        }
        kwargs.update(self.reasoning)
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id
        resp = await self.client.responses.create(**kwargs)
        calls = [
            ToolCall(item.call_id, item.name, item.arguments)
            for item in resp.output
            if item.type == "function_call"
        ]
        u = resp.usage
        return Decision(
            tool_calls=calls,
            response_id=resp.id,
            usage=Usage(u.input_tokens, u.output_tokens) if u else Usage(),
            cached_tokens=u.input_tokens_details.cached_tokens if u else 0,
            latency_ms=int((time.perf_counter() - start) * 1000),
            text=resp.output_text,
        )

    async def stream_answer(
        self, system: str, messages: list[dict[str, str]]
    ) -> AsyncIterator[AnswerChunk]:
        stream = await self.client.responses.create(
            model=self.answer_model,
            instructions=system,
            input=messages,  # type: ignore[arg-type]
            stream=True,
            **self.reasoning,
        )
        async for event in cast(AsyncStream[ResponseStreamEvent], stream):
            if isinstance(event, ResponseTextDeltaEvent):
                yield AnswerChunk(delta=event.delta)
            elif isinstance(event, ResponseCompletedEvent):
                u = event.response.usage
                if u:
                    yield AnswerChunk(
                        usage=Usage(u.input_tokens, u.output_tokens),
                        cached_tokens=u.input_tokens_details.cached_tokens,
                    )
