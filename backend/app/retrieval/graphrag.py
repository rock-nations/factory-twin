"""Vector search over past failures, enriched by graph traversal (spec §8.3)."""

from neo4j import Driver
from neo4j_graphrag.retrievers import VectorCypherRetriever

from app.core.embeddings import Embedder
from app.models.retrieval import SimilarIncident, SimilarIncidentsResult
from app.retrieval.embedder import QueryEmbedder
from app.retrieval.serialization import jsonable

OVERSAMPLE = 4

RETRIEVAL_QUERY = """
MATCH (node)-[:OCCURRED_ON]->(m:Machine)-[:IS_MODEL]->(mm:MachineModel)
WHERE $machine_model IS NULL OR mm.id = $machine_model
MATCH (node)-[:FAILED]->(c:Component)
OPTIONAL MATCH (node)-[:RESOLVED_BY]->(r:MaintenanceEvent)
OPTIONAL MATCH (r)-[:PERFORMED_BY]->(t:Technician)
OPTIONAL MATCH (d:Document)-[:DESCRIBES]->(node)
RETURN node.id AS failure_id, node.ts AS ts, node.description AS description,
       m.id AS machine_id, mm.id AS model, c.name AS component, score AS similarity,
       r.ts AS resolved_at, r.reason AS resolution_reason, t.name AS technician,
       d.id AS report_id, d.title AS report_title
"""


class IncidentFinder:
    def __init__(self, driver: Driver, embedder: Embedder) -> None:
        self.retriever = VectorCypherRetriever(
            driver, "failure_embedding", RETRIEVAL_QUERY, embedder=QueryEmbedder(embedder)
        )
        self.embedder = embedder

    def find(
        self, description: str, machine_model: str | None = None, top_k: int = 5
    ) -> SimilarIncidentsResult:
        raw = self.retriever.get_search_results(
            query_vector=self.embedder.embed([description])[0],
            top_k=top_k * OVERSAMPLE if machine_model else top_k,
            query_params={"machine_model": machine_model},
        )
        incidents = [SimilarIncident(**jsonable(r.data())) for r in raw.records][:top_k]
        if not incidents:
            return SimilarIncidentsResult(
                query=description, message="No similar past incidents found."
            )
        return SimilarIncidentsResult(query=description, incidents=incidents)
