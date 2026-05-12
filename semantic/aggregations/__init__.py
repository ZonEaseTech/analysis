"""Reusable aggregation primitives.

Pure functions consuming entity rows + grain configuration. Anything that does
business-specific stuff (BOM rollup, fallback name match, …) stays in the
report scripts — this layer should only know "how to GROUP BY in Python".
"""
from .by_grain import aggregate_by_grain

__all__ = ["aggregate_by_grain"]
