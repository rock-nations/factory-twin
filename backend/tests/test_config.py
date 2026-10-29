from app.core.config import Settings


def test_env_example_parses_with_inline_comments() -> None:
    from app.core.config import REPO_ROOT

    s = Settings(_env_file=REPO_ROOT / ".env.example")
    assert s.openai_orchestrator_model == ""
    assert s.openai_embedding_dimensions == 1536
    assert s.rag_min_score == 0.68
