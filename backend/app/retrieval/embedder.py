"""Adapter so neo4j-graphrag retrievers can use our cached OpenAI embedder."""

from neo4j_graphrag.embeddings.base import Embedder as GraphRagEmbedder

from app.core.embeddings import Embedder


class QueryEmbedder(GraphRagEmbedder):
    def __init__(self, embedder: Embedder) -> None:
        super().__init__()
        self._embedder = embedder

    def embed_query(self, text: str) -> list[float]:
        return self._embedder.embed([text])[0]
