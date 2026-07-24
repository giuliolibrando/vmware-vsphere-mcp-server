"""In-process inventory query engine: bulk property fetch, filter, count, format.

The list_* tools in server.py route through run_inventory_query() whenever the
caller supplies filters/group_by/fields. The engine fetches the needed
properties for ALL objects of a type in one PropertyCollector round-trip,
filters and aggregates in process, and returns only the result — so the
calling LLM never has to ingest the full inventory.
"""

from typing import List, Literal, Union

from pydantic import BaseModel


class FilterCondition(BaseModel):
    """One filter condition; conditions in a list are ANDed."""

    field: str
    op: Literal["eq", "ne", "contains", "not_contains",
                "gt", "gte", "lt", "lte", "in"]
    value: Union[str, int, float, bool, List[Union[str, int, float]]]


# ── Filter evaluation (pure) ─────────────────────────────────────

def _loose_eq(a, b) -> bool:
    """Equality that tolerates str/number/bool type mismatches from the LLM."""
    if isinstance(a, str) or isinstance(b, str):
        return str(a).lower() == str(b).lower()
    return a == b


def _matches(value, cond: FilterCondition) -> bool:
    op, expected = cond.op, cond.value
    if value is None:
        return op in ("ne", "not_contains")
    if op in ("eq", "ne"):
        equal = _loose_eq(value, expected)
        return equal if op == "eq" else not equal
    if op in ("contains", "not_contains"):
        hit = str(expected).lower() in str(value).lower()
        return hit if op == "contains" else not hit
    if op == "in":
        options = expected if isinstance(expected, list) else [expected]
        return any(_loose_eq(value, option) for option in options)
    try:
        left, right = float(value), float(expected)
    except (TypeError, ValueError):
        return False
    return {"gt": left > right, "gte": left >= right,
            "lt": left < right, "lte": left <= right}[op]


def apply_filters(rows: list, filters: list) -> list:
    """Return the rows matching ALL conditions."""
    return [row for row in rows
            if all(_matches(row.get(c.field), c) for c in filters)]
