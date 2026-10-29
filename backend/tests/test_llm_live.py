"""Live OpenAI checks. Skipped unless RUN_LLM_TESTS=1 (costs money)."""

import os

import pytest

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(os.environ.get("RUN_LLM_TESTS") != "1", reason="set RUN_LLM_TESTS=1"),
]


def test_semantic_search_bearing_overheating(clean_db, pdm_dir, test_settings, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Spec Phase 2: 'bearing overheating' -> spindle-bearing content in the top 3.

    Requires generated docs in data/generated/docs and a configured OPENAI_API_KEY.
    """
    from app.core.config import get_settings
    from app.core.embeddings import OpenAIEmbedder
    from app.ingestion.common import DEFAULT_GENERATED_DIR, run_read
    from app.ingestion.embed_documents import embed_documents
    from app.ingestion.init_schema import init_schema

    docs = DEFAULT_GENERATED_DIR / "docs"
    if not list(docs.glob("*.md")):
        pytest.skip("no generated documents yet")
    s = get_settings()
    embedder = OpenAIEmbedder(s, cache_path=tmp_path / "c.sqlite")
    init_schema(clean_db, test_settings)
    embed_documents(clean_db, embedder, docs)
    q = embedder.embed(["bearing overheating"])[0]
    rows = run_read(
        clean_db,
        "CALL db.index.vector.queryNodes('chunk_embedding', 3, $q) YIELD node, score "
        "RETURN node.text AS text",
        q=q,
    )
    assert any("bearing" in r["text"].lower() for r in rows)
