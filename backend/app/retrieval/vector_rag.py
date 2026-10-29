"""Hybrid (vector + fulltext) retrieval over document chunks (spec §8.3).

Similarity is Neo4j's `vector.similarity.cosine`, which is bounded to [0, 1] as (1 + cos) / 2,
so RAG_MIN_SCORE=0.75 corresponds to a raw cosine of 0.5.
"""

import re
from typing import Any, Literal

from neo4j import Driver
from neo4j_graphrag.retrievers import HybridCypherRetriever, VectorCypherRetriever

from app.core.config import Settings, get_settings
from app.core.embeddings import Embedder
from app.models.retrieval import RagChunk, RagResult, RagSource
from app.retrieval.embedder import QueryEmbedder
from app.retrieval.serialization import jsonable

OVERSAMPLE = 4
_LUCENE_SPECIAL = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')

# `node` (a Chunk) and `score` come from the hybrid search; the retriever supplies $query_vector.
RETRIEVAL_QUERY = """
MATCH (d:Document)-[:HAS_CHUNK]->(node)
WHERE ($doc_types IS NULL OR d.doc_type IN $doc_types)
  AND ($machine_model IS NULL OR d.machine_model IS NULL OR d.machine_model = $machine_model)
  AND ($component IS NULL OR d.component IS NULL OR d.component = $component)
OPTIONAL MATCH (prev:Chunk)-[:NEXT]->(node)
OPTIONAL MATCH (node)-[:NEXT]->(nxt:Chunk)
RETURN node.id AS chunk_id, node.text AS text, d.id AS doc_id, d.title AS title,
       d.doc_type AS doc_type, d.path AS path, score,
       vector.similarity.cosine(node.embedding, $query_vector) AS similarity,
       prev.text AS previous, nxt.text AS next,
       [(node)-[:ABOUT]->(t) | head(labels(t)) + ':' + t.id] AS about
"""


def lucene_safe(text: str) -> str:
    """Fulltext queries are Lucene syntax; user questions contain characters that break it."""
    return " ".join(_LUCENE_SPECIAL.sub(" ", text).split())


SearchMode = Literal["hybrid", "vector"]


class KnowledgeBase:
    """`search_mode="vector"` exists to evaluate vector-only against hybrid retrieval."""

    def __init__(
        self,
        driver: Driver,
        embedder: Embedder,
        settings: Settings | None = None,
        search_mode: SearchMode = "hybrid",
    ) -> None:
        self.settings = settings or get_settings()
        self.embedder = embedder
        self.search_mode = search_mode
        self.retriever: HybridCypherRetriever | VectorCypherRetriever
        if search_mode == "hybrid":
            self.retriever = HybridCypherRetriever(
                driver,
                "chunk_embedding",
                "chunk_fulltext",
                RETRIEVAL_QUERY,
                embedder=QueryEmbedder(embedder),
            )
        else:
            self.retriever = VectorCypherRetriever(
                driver, "chunk_embedding", RETRIEVAL_QUERY, embedder=QueryEmbedder(embedder)
            )

    def search(
        self,
        query: str,
        doc_types: list[str] | None = None,
        machine_model: str | None = None,
        component: str | None = None,
        top_k: int = 5,
        min_similarity: float | None = None,
    ) -> RagResult:
        threshold = self.settings.rag_min_score if min_similarity is None else min_similarity
        params: dict[str, Any] = {
            "doc_types": doc_types or None,
            "machine_model": machine_model,
            "component": component,
        }
        vector = self.embedder.embed([query])[0]
        fetch = top_k * OVERSAMPLE  # filters/threshold are applied after the index lookup
        if isinstance(self.retriever, HybridCypherRetriever):
            raw = self.retriever.get_search_results(
                query_text=lucene_safe(query) or query,
                query_vector=vector,
                top_k=fetch,
                query_params=params,
            )
        else:
            raw = self.retriever.get_search_results(
                query_vector=vector, top_k=fetch, query_params=params
            )
        chunks = [
            RagChunk(**jsonable(r.data()))
            for r in raw.records
            if r["similarity"] is not None and r["similarity"] >= threshold
        ][:top_k]
        if not chunks:
            return RagResult(
                query=query,
                no_relevant_documents=True,
                message="No relevant documents found in the knowledge base.",
            )
        best: dict[str, RagSource] = {}
        for c in chunks:
            if c.doc_id not in best or c.similarity > best[c.doc_id].best_similarity:
                best[c.doc_id] = RagSource(
                    doc_id=c.doc_id,
                    title=c.title,
                    doc_type=c.doc_type,
                    best_similarity=c.similarity,
                )
        return RagResult(query=query, chunks=chunks, sources=list(best.values()))
