"""Metric registry — single source of truth for every 口径 (metric).

Each metric is defined once in semantic/metrics/registry/*.yaml and consumed by:
  - render_catalog.py  → generates docs/metrics-catalog.md (drift dies)
  - hub `metrics` API  → renders the 口径中心 page from structured data
  - report yaml `metric:` bindings → pull the 口径 comment by id

See docs/plan/2026-06-15-metrics-registry.md for the design.
"""
from __future__ import annotations

from .loader import load_registry, registry_by_domain
from .schema import (
    CONFIDENCE,
    DOMAINS,
    STATUS,
    Formula,
    Lineage,
    Metric,
    Reconciliation,
    metric_from_dict,
    validate_registry,
)

__all__ = [
    "load_registry",
    "registry_by_domain",
    "Metric",
    "Formula",
    "Lineage",
    "Reconciliation",
    "metric_from_dict",
    "validate_registry",
    "DOMAINS",
    "CONFIDENCE",
    "STATUS",
]
