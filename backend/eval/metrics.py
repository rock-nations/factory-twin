"""Pure metric helpers for the evaluation harness (no I/O, unit-tested)."""

import math
from collections.abc import Iterable, Sequence
from typing import Any


def _norm(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float):
        return round(float(value), 1)  # generated queries may or may not round averages
    if isinstance(value, list):
        return tuple(sorted((_norm(v) for v in value), key=repr))
    if isinstance(value, dict):
        return tuple(sorted(((k, _norm(v)) for k, v in value.items()), key=repr))
    return str(value).strip().lower()


def rows_as_set(rows: Sequence[dict[str, Any]]) -> set[tuple[Any, ...]]:
    """Order-insensitive, alias-insensitive view of a result: a set of value tuples."""
    return {tuple(sorted((_norm(v) for v in row.values()), key=repr)) for row in rows}


def result_sets_match(gold: Sequence[dict[str, Any]], got: Sequence[dict[str, Any]]) -> bool:
    return rows_as_set(gold) == rows_as_set(got)


def result_contains_gold(gold: Sequence[dict[str, Any]], got: Sequence[dict[str, Any]]) -> bool:
    """Lenient: every gold row's values appear together in some generated row.

    Accepts extra columns and extra rows (a more informative query), so it is meant to be reported
    next to the strict set equality, never instead of it.
    """
    if not gold:
        return not got
    produced = [{_norm(v) for v in row.values()} for row in got]
    return all(any({_norm(v) for v in g.values()} <= row for row in produced) for g in gold)


def hit_at_k(expected_any: Iterable[str], retrieved: Sequence[str], k: int = 5) -> bool:
    wanted = set(expected_any)
    return bool(wanted) and any(doc in wanted for doc in retrieved[:k])


def keyword_recall(keywords: Sequence[str], answer: str) -> float | None:
    """Fraction of keywords present (case-insensitive); None when there is nothing to check."""
    if not keywords:
        return None
    text = answer.lower()
    return sum(1 for k in keywords if k.lower() in text) / len(keywords)


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile (pct in 0..100)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def gold_keywords(gold_rows: Sequence[dict[str, Any]], limit: int = 3) -> list[str]:
    """Strings from the gold result the answer should mention (first column of the first rows)."""
    out: list[str] = []
    for row in gold_rows[:limit]:
        for value in row.values():
            if isinstance(value, str | int | float) and not isinstance(value, bool):
                out.append(f"{value:g}" if isinstance(value, float) else str(value))
                break
    return out
