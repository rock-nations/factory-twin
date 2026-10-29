from typing import Any

from pydantic import BaseModel, Field


class QueryResult(BaseModel):
    """Outcome of running one guarded, read-only Cypher query."""

    cypher: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    error: str | None = None


class Text2CypherResult(QueryResult):
    question: str = ""
    attempts: int = 1
    input_tokens: int = 0
    output_tokens: int = 0


class RagChunk(BaseModel):
    chunk_id: str
    text: str
    doc_id: str
    title: str
    doc_type: str
    path: str | None = None
    similarity: float
    score: float
    previous: str | None = None
    next: str | None = None
    about: list[str] = Field(default_factory=list)


class RagSource(BaseModel):
    doc_id: str
    title: str
    doc_type: str
    best_similarity: float


class RagResult(BaseModel):
    query: str
    chunks: list[RagChunk] = Field(default_factory=list)
    sources: list[RagSource] = Field(default_factory=list)
    no_relevant_documents: bool = False
    message: str | None = None


class SimilarIncident(BaseModel):
    failure_id: str
    ts: str | None = None
    description: str | None = None
    machine_id: str
    model: str
    component: str
    similarity: float
    resolved_at: str | None = None
    resolution_reason: str | None = None
    technician: str | None = None
    report_id: str | None = None
    report_title: str | None = None


class SimilarIncidentsResult(BaseModel):
    query: str
    incidents: list[SimilarIncident] = Field(default_factory=list)
    message: str | None = None
