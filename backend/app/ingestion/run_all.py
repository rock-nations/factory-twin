"""Idempotent end-to-end load. Later phases add document generation / embedding steps."""

import argparse
import logging

from neo4j import Driver

from app.core.config import get_settings
from app.core.embeddings import OpenAIEmbedder
from app.core.neo4j import create_sync_driver
from app.ingestion.build_topology import build_topology
from app.ingestion.common import run_read
from app.ingestion.compute_thresholds import compute_and_store_thresholds
from app.ingestion.embed_documents import embed_documents
from app.ingestion.embed_failures import embed_failures
from app.ingestion.init_schema import init_schema
from app.ingestion.load_events import load_events

logger = logging.getLogger(__name__)


def graph_counts(driver: Driver) -> tuple[dict[str, int], dict[str, int]]:
    nodes = run_read(
        driver, "MATCH (n) UNWIND labels(n) AS l RETURN l AS k, count(*) AS c ORDER BY k"
    )
    rels = run_read(driver, "MATCH ()-[r]->() RETURN type(r) AS k, count(*) AS c ORDER BY k")
    return {r["k"]: r["c"] for r in nodes}, {r["k"]: r["c"] for r in rels}


def run_all(driver: Driver, embeddings: bool = True) -> tuple[dict[str, int], dict[str, int]]:
    init_schema(driver)
    build_topology(driver)
    load_events(driver)
    compute_and_store_thresholds(driver)
    if embeddings:
        embedder = OpenAIEmbedder()
        embed_documents(driver, embedder)
        embed_failures(driver, embedder)
    return graph_counts(driver)


def main() -> None:
    p = argparse.ArgumentParser(description="Idempotent end-to-end load.")
    p.add_argument(
        "--skip-embeddings", action="store_true", help="structure only (no OpenAI calls)"
    )
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Documents are generated separately (costs money): python -m app.ingestion.generate_documents
    embeddings = not args.skip_embeddings and bool(get_settings().openai_api_key)
    if not embeddings and not args.skip_embeddings:
        logger.warning("OPENAI_API_KEY not set: skipping document/failure embeddings")
    driver = create_sync_driver()
    try:
        nodes, rels = run_all(driver, embeddings)
    finally:
        driver.close()
    print("\nNodes:")
    for k, c in nodes.items():
        print(f"  {k:<18}{c:>8}")
    print("Relationships:")
    for k, c in rels.items():
        print(f"  {k:<18}{c:>8}")


if __name__ == "__main__":
    main()
