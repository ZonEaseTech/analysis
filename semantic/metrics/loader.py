"""Load the metric registry from semantic/metrics/registry/*.yaml.

One yaml file per business domain (sales / settlement / finance / kpi /
metadata). Each file is a list under top-level key `metrics`. Order within
a file is preserved; domains render in DOMAINS declaration order.
"""
from __future__ import annotations

import os

import yaml

from .schema import DOMAINS, Metric, metric_from_dict, validate_registry

# semantic/metrics/registry/
REGISTRY_DIR = os.path.join(os.path.dirname(__file__), "registry")


def load_registry(registry_dir: str = REGISTRY_DIR) -> list[Metric]:
    """Load + validate every metric across all domain yaml files.

    Returns metrics ordered by DOMAINS declaration order, then by their
    in-file order. Raises ValueError on any schema / integrity violation.
    """
    by_domain: dict[str, list[Metric]] = {d: [] for d in DOMAINS}
    extra: list[Metric] = []

    for fname in sorted(os.listdir(registry_dir)):
        if not fname.endswith((".yaml", ".yml")):
            continue
        path = os.path.join(registry_dir, fname)
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        items = raw.get("metrics") or []
        if not isinstance(items, list):
            raise ValueError(f"{fname}: top-level 'metrics' must be a list")
        for item in items:
            m = metric_from_dict(item)
            by_domain.setdefault(m.domain, extra if m.domain not in by_domain else by_domain[m.domain])
            by_domain[m.domain].append(m)

    ordered: list[Metric] = []
    for d in DOMAINS:
        ordered.extend(by_domain.get(d, []))

    validate_registry(ordered)
    return ordered


def registry_by_domain(metrics: list[Metric] | None = None) -> dict[str, list[Metric]]:
    """Group metrics by domain, preserving DOMAINS order with empty domains kept."""
    metrics = metrics if metrics is not None else load_registry()
    out: dict[str, list[Metric]] = {d: [] for d in DOMAINS}
    for m in metrics:
        out.setdefault(m.domain, []).append(m)
    return out
