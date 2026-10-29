import re

from app.graph import constants as C
from app.ingestion.init_schema import SCHEMA_FILE, load_statements

LLM_SCHEMA = (SCHEMA_FILE.parent / "schema_for_llm.md").read_text()


def test_every_label_has_a_unique_id_constraint() -> None:
    text = SCHEMA_FILE.read_text()
    labelled = set(re.findall(r"FOR \(n:(\w+)\) REQUIRE n\.id IS UNIQUE", text))
    assert labelled == set(C.NODE_LABELS)


def test_embedding_dimensions_are_templated() -> None:
    stmts = "\n".join(load_statements(768))
    assert "`vector.dimensions`: 768" in stmts
    assert "{{" not in stmts


def test_llm_schema_mentions_every_label_and_relationship() -> None:
    for label in C.NODE_LABELS:
        assert f":{label}" in LLM_SCHEMA, label
    for rel in C.RELATIONSHIP_TYPES:
        assert f":{rel}" in LLM_SCHEMA, rel


def test_llm_schema_uses_the_shared_component_names() -> None:
    for name in C.COMPONENTS.values():
        assert name in LLM_SCHEMA
