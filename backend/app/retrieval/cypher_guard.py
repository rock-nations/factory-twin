"""Safety layer for LLM-generated Cypher: validate, cap, EXPLAIN, run in a READ transaction.

Community Edition has no role-based access control, so this guard plus the READ transaction
(`session.execute_read`) is the protection layer. LLM Cypher must never reach a write transaction.
"""

import logging
import re
from typing import Any

from neo4j import READ_ACCESS, Driver, unit_of_work
from neo4j.exceptions import Neo4jError

from app.models.retrieval import QueryResult
from app.retrieval.serialization import jsonable

logger = logging.getLogger(__name__)

MAX_ROWS = 50
DEFAULT_TIMEOUT_S = 10.0

_FORBIDDEN = re.compile(
    r"""
    \b(?:CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|LOAD\s+CSV)\b
    | \bCALL\s+(?:dbms|db\.(?:create|drop|clear|index\.fulltext\.(?:create|drop)))
    | \bapoc\.(?!(?:coll|text|math|map|date|convert|agg|temporal|number)\.)
    | \bUSING\s+PERIODIC\s+COMMIT\b
    | \bIN\s+TRANSACTIONS\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
# procedures that are fine for read-only schema introspection
_ALLOWED_PROCEDURES = {"db.labels", "db.relationshiptypes", "db.propertykeys"}
_CALL_PROC = re.compile(r"\bCALL\s+([A-Za-z_][\w.]*)", re.IGNORECASE)
_RETURN = re.compile(r"\bRETURN\b", re.IGNORECASE)
_LIMIT = re.compile(r"\bLIMIT\b", re.IGNORECASE)


class CypherGuardError(ValueError):
    """The query was rejected before reaching the database."""


def blank_literals(query: str) -> str:
    """Replace string literals, backticked names and comments with spaces (same length)."""
    out: list[str] = []
    i, n = 0, len(query)
    while i < n:
        c = query[i]
        if query.startswith("//", i):
            j = query.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif query.startswith("/*", i):
            j = query.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(" " * (j - i))
            i = j
        elif c in "'\"`":
            j = i + 1
            while j < n and query[j] != c:
                j += 2 if query[j] == "\\" and c != "`" else 1
            j = min(j + 1, n)
            out.append(c + " " * (j - i - 2) + c if j - i >= 2 else c)
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def guard(cypher: str, max_rows: int = MAX_ROWS) -> str:
    """Return the query to run (trailing `;` removed, LIMIT appended when missing) or raise."""
    query = cypher.strip()
    if not query:
        raise CypherGuardError("empty query")
    blanked = blank_literals(query)
    stripped = blanked.rstrip().rstrip(";").rstrip()
    if ";" in stripped:
        raise CypherGuardError("multiple statements are not allowed")
    if m := _FORBIDDEN.search(stripped):
        raise CypherGuardError(f"write/admin operation not allowed: {m.group(0).strip()}")
    for proc in _CALL_PROC.findall(stripped):
        if proc.lower() not in _ALLOWED_PROCEDURES:
            raise CypherGuardError(f"procedure call not allowed: {proc}")
    if not _RETURN.search(stripped):
        raise CypherGuardError("query must end in a RETURN clause")

    query = query[: len(stripped)]  # drops trailing whitespace/semicolon (same offsets)
    last_return = list(_RETURN.finditer(stripped))[-1]
    if not _LIMIT.search(stripped[last_return.end() :]):
        query = f"{query}\nLIMIT {max_rows}"
    return query


def run_guarded(
    driver: Driver,
    cypher: str,
    *,
    max_rows: int = MAX_ROWS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    params: dict[str, Any] | None = None,
) -> QueryResult:
    """Guard -> EXPLAIN -> execute in a READ transaction. Never raises for bad Cypher."""
    try:
        safe = guard(cypher, max_rows)
    except CypherGuardError as exc:
        return QueryResult(cypher=cypher, error=f"rejected by guard: {exc}")

    @unit_of_work(timeout=timeout_s)
    def explain(tx: Any) -> None:
        tx.run("EXPLAIN " + safe, params or {}).consume()

    @unit_of_work(timeout=timeout_s)
    def execute(tx: Any) -> list[dict[str, Any]]:
        result = tx.run(safe, params or {})
        rows = []
        for record in result:
            rows.append(record.data())
            if len(rows) > max_rows:
                break
        return rows

    try:
        with driver.session(default_access_mode=READ_ACCESS) as session:
            session.execute_read(explain)
            rows = session.execute_read(execute)
    except Neo4jError as exc:
        return QueryResult(cypher=safe, error=f"{exc.code}: {exc.message}")
    except Exception as exc:
        logger.exception("unexpected error running guarded query")
        return QueryResult(cypher=safe, error=f"{type(exc).__name__}: {exc}")

    truncated = len(rows) > max_rows
    rows = [jsonable(r) for r in rows[:max_rows]]
    return QueryResult(cypher=safe, rows=rows, row_count=len(rows), truncated=truncated)
