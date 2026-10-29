import pytest

from app.retrieval.cypher_guard import CypherGuardError, blank_literals, guard


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (n) CREATE (m:X) RETURN m",
        "MATCH (n) DETACH DELETE n RETURN 1",
        "MATCH (n:Machine) SET n.status = 'x' RETURN n",
        "MATCH (n) REMOVE n.age RETURN n",
        "MERGE (n:Machine {id: 'x'}) RETURN n",
        "MATCH (n) FOREACH (x IN [1] | CREATE (:A)) RETURN n",
        "LOAD CSV FROM 'file:///x.csv' AS r RETURN r",
        "CALL dbms.security.createUser('a','b') RETURN 1",
        "CALL db.createLabel('X') RETURN 1",
        "CALL apoc.create.node(['A'], {}) YIELD node RETURN node",
        "CALL apoc.periodic.iterate('a','b',{}) YIELD batches RETURN batches",
        "CALL apoc.cypher.doIt('CREATE (n)', {}) YIELD value RETURN value",
        "match (n) create (m) return m",
        "MATCH (n) RETURN n; MATCH (m) RETURN m",
        "MATCH (n) RETURN n; DROP CONSTRAINT x",
        "CALL db.index.vector.queryNodes('chunk_embedding', 5, [1.0]) YIELD node RETURN node",
        "MATCH (n) WHERE n.id = 'x' MATCH (m) CALL { WITH m MERGE (z:Z) } RETURN m",
        "MATCH (n) RETURN n /* ok */ ; CREATE (x)",
        "MATCH (n:Machine)",  # no RETURN
        "   ",
    ],
)
def test_rejects_unsafe_or_malformed(q: str) -> None:
    with pytest.raises(CypherGuardError):
        guard(q)


def test_keywords_inside_string_literals_are_allowed() -> None:
    q = "MATCH (a:Alarm) WHERE a.message CONTAINS 'create and delete; SET x' RETURN a.id"
    assert guard(q).startswith(
        "MATCH (a:Alarm) WHERE a.message CONTAINS 'create and delete; SET x'"
    )


def test_double_quoted_and_escaped_literals() -> None:
    q = r"""MATCH (a:Alarm) WHERE a.message = "it\"s a MERGE; DROP" RETURN a.id"""
    assert "LIMIT 50" in guard(q)


def test_keywords_inside_backticks_and_comments_are_allowed() -> None:
    assert "LIMIT" in guard("MATCH (n) // CREATE this later\nRETURN n.`set` AS v")
    assert "LIMIT" in guard("MATCH (n) /* DELETE */ RETURN n")


def test_property_and_label_names_that_contain_keywords_are_allowed() -> None:
    q = "MATCH (n:Machine) WHERE n.created_by = 'x' OR n.dataset = 1 RETURN n.reset AS offset"
    assert "LIMIT 50" in guard(q)


def test_limit_appended_when_missing_and_semicolon_dropped() -> None:
    assert guard("MATCH (m:Machine) RETURN m.id;") == "MATCH (m:Machine) RETURN m.id\nLIMIT 50"


def test_existing_limit_is_kept() -> None:
    q = "MATCH (m:Machine) RETURN m.id ORDER BY m.id LIMIT 5"
    assert guard(q) == q


def test_limit_in_where_before_return_does_not_count() -> None:
    q = "MATCH (m:Machine) WHERE m.id <> 'limit' RETURN m.id"
    assert guard(q).endswith("LIMIT 50")


def test_limit_inside_literal_does_not_count() -> None:
    assert guard("MATCH (m) RETURN 'LIMIT' AS x").endswith("LIMIT 50")


def test_subquery_limit_does_not_hide_missing_final_limit() -> None:
    q = "MATCH (m:Machine) CALL { WITH m MATCH (m)-->(x) RETURN x LIMIT 3 } RETURN m.id, x.id"
    assert guard(q).endswith("LIMIT 50")


def test_custom_max_rows() -> None:
    assert guard("MATCH (n) RETURN n", max_rows=10).endswith("LIMIT 10")


def test_schema_introspection_procedures_are_allowed() -> None:
    assert guard("CALL db.labels() YIELD label RETURN label")


def test_read_only_apoc_helpers_are_allowed() -> None:
    assert guard("MATCH (m:Machine) RETURN apoc.text.join(['a','b'], ',') AS x")


def test_blank_literals_preserves_length() -> None:
    q = "RETURN 'abc' AS a, \"d'e\" AS b, `x y` AS c // tail\n"
    assert len(blank_literals(q)) == len(q)
    assert "abc" not in blank_literals(q)
