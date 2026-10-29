"""Chat API models: request/response, trace, and the /ws/chat event protocol (spec §9)."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

Route = Literal["cypher", "rag", "hybrid", "direct"]
SourceKind = Literal["cypher", "document", "incident", "machine"]


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)


class SourceRef(BaseModel):
    ref: int
    kind: SourceKind
    title: str
    doc_id: str | None = None
    doc_type: str | None = None


class ToolTrace(BaseModel):
    name: str
    arguments: dict[str, Any]
    summary: str
    cypher: str | None = None
    row_count: int | None = None
    truncated: bool = False
    preview_rows: list[dict[str, Any]] = Field(default_factory=list)  # first 10 rows for the UI
    sources: list[SourceRef] = Field(default_factory=list)
    retrieval_scores: list[float] = Field(default_factory=list)
    latency_ms: int = 0
    error: str | None = None


class LLMCall(BaseModel):
    step: str  # orchestrate | text2cypher | answer | proactive
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


class Trace(BaseModel):
    route: Route = "direct"
    tools: list[ToolTrace] = Field(default_factory=list)
    llm_calls: list[LLMCall] = Field(default_factory=list)
    rounds: int = 0
    total_latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    step_latency_ms: dict[str, int] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    answer: str
    trace: Trace


# ---- WebSocket events (server -> client) ----------------------------------------------------


class RouteEvent(BaseModel):
    type: Literal["route"] = "route"
    route: Route


class ToolCallEvent(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    name: str
    args: dict[str, Any]


class ToolResultEvent(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    name: str
    summary: str
    cypher: str | None = None
    row_count: int | None = None
    sources: list[SourceRef] = Field(default_factory=list)


class TokenEvent(BaseModel):
    type: Literal["token"] = "token"
    delta: str


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    answer: str
    trace: Trace


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


class ProactiveAction(BaseModel):
    label: str
    message: str  # prefilled chat message when the user clicks the quick action


class ProactiveEvent(BaseModel):
    type: Literal["proactive"] = "proactive"
    message: str
    actions: list[ProactiveAction] = Field(default_factory=list)
    machine_id: str | None = None


ChatEvent = Annotated[
    RouteEvent
    | ToolCallEvent
    | ToolResultEvent
    | TokenEvent
    | DoneEvent
    | ErrorEvent
    | ProactiveEvent,
    Field(discriminator="type"),
]
