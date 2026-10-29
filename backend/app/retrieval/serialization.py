"""Neo4j values -> JSON-safe Python (for LLM context, traces and API responses)."""

from typing import Any

from neo4j.graph import Node, Path, Relationship
from neo4j.time import Date, DateTime, Duration, Time

_DROP_KEYS = {"embedding"}  # never ship vectors to the LLM / UI


def jsonable(value: Any) -> Any:
    if isinstance(value, DateTime):
        return value.iso_format()
    if isinstance(value, Date | Time):
        return value.iso_format()
    if isinstance(value, Duration):
        return str(value)
    if isinstance(value, Node):
        return {"_labels": sorted(value.labels), **jsonable(dict(value))}
    if isinstance(value, Relationship):
        return {"_type": value.type, **jsonable(dict(value))}
    if isinstance(value, Path):
        return [jsonable(n) for n in value.nodes]
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items() if k not in _DROP_KEYS}
    if isinstance(value, list | tuple | set):
        return [jsonable(v) for v in value]
    return value
