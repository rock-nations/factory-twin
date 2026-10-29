"""Chunk + embed generated markdown documents into Document/Chunk nodes (spec §6).

Unchanged documents (same content_hash) are skipped, so re-runs embed nothing.
"""

import argparse
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import frontmatter
from neo4j import Driver

from app.core.config import REPO_ROOT
from app.core.embeddings import Embedder, OpenAIEmbedder
from app.core.neo4j import create_sync_driver
from app.ingestion.chunker import chunk_markdown
from app.ingestion.common import DEFAULT_GENERATED_DIR, run_read, run_write

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedDoc:
    path: Path
    meta: dict[str, Any]
    body: str
    content_hash: str


def parse_document(path: Path) -> ParsedDoc:
    raw = path.read_bytes()
    post = frontmatter.loads(raw.decode("utf-8"))
    for key in ("doc_id", "doc_type", "title"):
        if not post.metadata.get(key):
            raise ValueError(f"{path.name}: front-matter is missing '{key}'")
    return ParsedDoc(path, dict(post.metadata), post.content, hashlib.sha256(raw).hexdigest())


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def embed_document(driver: Driver, embedder: Embedder, doc: ParsedDoc) -> int:
    meta = doc.meta
    doc_id = meta["doc_id"]
    chunks = chunk_markdown(doc.body)
    vectors = embedder.embed([f"{meta['title']}\n\n{c.text}" for c in chunks])

    run_write(
        driver, "MATCH (:Document {id: $id})-[:HAS_CHUNK]->(c:Chunk) DETACH DELETE c", id=doc_id
    )
    run_write(
        driver,
        """
        MERGE (d:Document {id: $id})
        SET d.title = $title, d.doc_type = $doc_type, d.path = $path,
            d.content_hash = $hash, d.synthetic = true,
            d.machine_model = $model, d.component = $component, d.error_type = $error
        WITH d
        UNWIND $chunks AS ch
        MERGE (c:Chunk {id: ch.id})
        SET c.text = ch.text, c.seq = ch.seq, c.token_count = ch.token_count,
            c.embedding = ch.embedding
        MERGE (d)-[:HAS_CHUNK]->(c)
        """,
        id=doc_id,
        title=meta["title"],
        doc_type=meta["doc_type"],
        path=_display_path(doc.path),
        hash=doc.content_hash,
        model=meta.get("machine_model"),
        component=meta.get("component"),
        error=meta.get("error_type"),
        chunks=[
            {
                "id": f"{doc_id}-{c.seq:03d}",
                "seq": c.seq,
                "text": c.text,
                "token_count": c.token_count,
                "embedding": v,
            }
            for c, v in zip(chunks, vectors, strict=True)
        ],
    )
    run_write(
        driver,
        """
        MATCH (:Document {id: $id})-[:HAS_CHUNK]->(a:Chunk),
              (:Document {id: $id})-[:HAS_CHUNK]->(b:Chunk)
        WHERE b.seq = a.seq + 1
        MERGE (a)-[:NEXT]->(b)
        """,
        id=doc_id,
    )
    # ABOUT links to real graph entities named in the front-matter
    about_queries = []
    if meta.get("machine_model"):
        about_queries.append(("MachineModel", meta["machine_model"]))
    if meta.get("error_type"):
        about_queries.append(("ErrorType", meta["error_type"]))
    for label, target in about_queries:
        run_write(
            driver,
            f"""
            MATCH (:Document {{id: $id}})-[:HAS_CHUNK]->(c:Chunk), (t:{label} {{id: $target}})
            MERGE (c)-[:ABOUT]->(t)
            """,
            id=doc_id,
            target=target,
        )
    if meta.get("failure_id"):
        # incident report: describes the failure; chunks are about the component that failed
        run_write(
            driver,
            """
            MATCH (d:Document {id: $id}), (f:Failure {id: $fid})
            MERGE (d)-[:DESCRIBES]->(f)
            WITH d, f
            MATCH (f)-[:FAILED]->(comp:Component), (d)-[:HAS_CHUNK]->(c:Chunk)
            MERGE (c)-[:ABOUT]->(comp)
            """,
            id=doc_id,
            fid=meta["failure_id"],
        )
    return len(chunks)


def embed_documents(
    driver: Driver, embedder: Embedder, docs_dir: Path = DEFAULT_GENERATED_DIR / "docs"
) -> dict[str, int]:
    existing = {
        r["id"]: r["hash"]
        for r in run_read(driver, "MATCH (d:Document) RETURN d.id AS id, d.content_hash AS hash")
    }
    stats = {"documents": 0, "embedded": 0, "skipped": 0, "chunks": 0}
    for path in sorted(docs_dir.glob("*.md")):
        doc = parse_document(path)
        stats["documents"] += 1
        if existing.get(doc.meta["doc_id"]) == doc.content_hash:
            stats["skipped"] += 1
            continue
        stats["chunks"] += embed_document(driver, embedder, doc)
        stats["embedded"] += 1
    logger.info("Documents: %s", stats)
    return stats


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--docs-dir", type=Path, default=DEFAULT_GENERATED_DIR / "docs")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    driver = create_sync_driver()
    try:
        print(embed_documents(driver, OpenAIEmbedder(), args.docs_dir))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
