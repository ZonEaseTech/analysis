"""Report column → metric bindings + the contract gate.

A report yaml column may carry `metric: <id>` to declare which registry 口径
it materializes. This module scans every resources/reports/*.yaml for those
bindings and validates each id resolves to a real metric — the CI contract
that keeps reports honest about where their numbers come from.

    from semantic.metrics.report_bindings import scan_report_bindings, validate_bindings
    bindings = scan_report_bindings()
    validate_bindings(bindings)        # raises ValueError on a dangling id
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

from .loader import load_registry

_HERE = os.path.dirname(__file__)
REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
REPORTS_DIR = os.path.join(REPO_ROOT, "resources", "reports")


@dataclass
class Binding:
    """One column in one report sheet bound to a registry metric id."""
    report: str        # yaml filename, e.g. "profit_by_price.yaml"
    sheet: str         # sheet name, e.g. "套餐"
    column: str        # column header, e.g. "实收金额"
    field_index: int
    metric_id: str


def scan_report_bindings(reports_dir: str = REPORTS_DIR) -> list[Binding]:
    """Collect every column carrying a `metric:` key across all report yamls."""
    out: list[Binding] = []
    if not os.path.isdir(reports_dir):
        return out
    for fname in sorted(os.listdir(reports_dir)):
        if not fname.endswith((".yaml", ".yml")):
            continue
        with open(os.path.join(reports_dir, fname), "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        for sheet_name, sheet in (cfg.get("sheets") or {}).items():
            for col in (sheet or {}).get("columns", []) or []:
                mid = col.get("metric")
                if mid:
                    out.append(Binding(
                        report=fname,
                        sheet=sheet_name,
                        column=col.get("name", ""),
                        field_index=col.get("field_index", -1),
                        metric_id=mid,
                    ))
    return out


def validate_bindings(
    bindings: list[Binding] | None = None,
    known_ids: set[str] | None = None,
) -> None:
    """Every bound metric id must exist in the registry. Raises ValueError."""
    bindings = bindings if bindings is not None else scan_report_bindings()
    if known_ids is None:
        known_ids = {m.id for m in load_registry()}
    dangling = [b for b in bindings if b.metric_id not in known_ids]
    if dangling:
        lines = "\n".join(
            f"  {b.report} / {b.sheet} / {b.column} → unknown metric {b.metric_id!r}"
            for b in dangling
        )
        raise ValueError(f"report metric bindings reference unknown ids:\n{lines}")
