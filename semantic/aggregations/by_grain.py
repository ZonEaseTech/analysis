"""Group entity rows by an arbitrary subset of dimensions, SUM the metrics.

The whole point: writing a new report = picking a grain. SQL doesn't change,
aggregate.py doesn't change, only the yaml + a tiny entry script change.

Design choices worth knowing:

  - Pure function. No globals, no I/O, no side effects → trivially testable
    and trivially composable with the validator.
  - Tuple keys (not nested dicts) so multi-dim grain works without nesting
    `agg[store][item][price] = …` pyramids.
  - Returns `dict[tuple, dict]` not `list[dict]` so callers can index by key.
    Use `list(result.values())` (or `result.items()` if you need the keys)
    when feeding to row builders / validators.
  - Missing metrics treated as 0 — keeps the function safe against schema
    drift on the entity side.
"""
from __future__ import annotations

from typing import Any, Iterable


def aggregate_by_grain(
    rows: Iterable[Any],
    grain_keys: list[str],
    metric_keys: list[str],
) -> dict[tuple, dict]:
    """Group `rows` by the given `grain_keys` (in order), SUMming `metric_keys`.

    Args:
        rows: iterable of row-like objects (anything supporting getattr or
            dict-style access). BQ Rows, SimpleNamespace, plain dicts all work.
        grain_keys: dimension columns to GROUP BY, in the order they should
            appear in the result key tuple.
        metric_keys: numeric columns to SUM. Missing values count as 0.

    Returns:
        dict mapping `tuple(grain_values)` → dict of `{metric_key: sum}`.

    Example:
        >>> events = [
        ...     {"item": "A", "price": 10, "channel": "dine",    "qty": 5, "revenue": 50},
        ...     {"item": "A", "price": 10, "channel": "takeout", "qty": 3, "revenue": 30},
        ...     {"item": "A", "price": 12, "channel": "dine",    "qty": 2, "revenue": 24},
        ... ]
        >>> aggregate_by_grain(events, ["item", "price"], ["qty", "revenue"])
        {('A', 10): {'qty': 8, 'revenue': 80}, ('A', 12): {'qty': 2, 'revenue': 24}}
    """
    if not grain_keys:
        raise ValueError("grain_keys must be non-empty")
    if not metric_keys:
        raise ValueError("metric_keys must be non-empty")

    result: dict[tuple, dict] = {}
    for row in rows:
        key = tuple(_read(row, k) for k in grain_keys)
        bucket = result.get(key)
        if bucket is None:
            bucket = {m: 0.0 for m in metric_keys}
            result[key] = bucket
        for m in metric_keys:
            val = _read(row, m, default=0)
            try:
                bucket[m] += float(val or 0)
            except (TypeError, ValueError):
                # Non-numeric metric value: skip, keep aggregate sane.
                # Surface as 0 contribution; caller can audit downstream.
                pass
    return result


def _read(row: Any, key: str, default: Any = None) -> Any:
    """Uniform accessor: works with object attrs (BQ Row, SimpleNamespace)
    and dict-likes. Returns `default` if neither has the key."""
    if hasattr(row, key):
        return getattr(row, key)
    if isinstance(row, dict):
        return row.get(key, default)
    return default
