"""Semantic layer — business entities, metrics, and dimensions.

This package answers "what is营业额 / what is 实收金额 / what is a sale_line"
exactly once. Reports compose primitives from here instead of redefining
SQL fragments per-script.

Sub-packages:
  entities/    business objects as reusable SQL CTE factories
  metrics/     named field-level semantics (descriptions used by yaml comments)
  dimensions/  store / time / product key helpers

Stable contract: every entity-CTE function returns a string with
`{project}`, `{dataset}`, `{start_ts}`, `{end_ts}` placeholders intact,
so downstream `.format(...)` works the same way the old monolithic
templates did.
"""
