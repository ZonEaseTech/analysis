"""Validators — accounting-style identity checks for report rows.

Design DNA matches dbt-utils.expression_is_true + Soda Checks:
  - Declarative `Identity` (lhs/rhs/classify) — equivalent to dbt's
    `expression` + `severity` config or Soda's `sum_check` clauses.
  - Three-tier `Severity` (NEGLIGIBLE / NEEDS_REVIEW / MUST_FIX) — matches
    Soda's `pass / warn / fail` exactly.
  - Per-identity classification function allows different tolerance per
    rule (integer identities = zero tolerance; money identities = absolute
    + relative floor).

Output sink in this version is **console only** — no persisted artifact,
no customer-facing sheet. Single-dev / CLI-driven workflow.
"""

from .core import Identity, Severity, Result, check, print_result
from . import identities

__all__ = [
    "Identity", "Severity", "Result", "check", "print_result",
    "identities",
]
