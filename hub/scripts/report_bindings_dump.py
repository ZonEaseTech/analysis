"""Dump report column → metric bindings (joined with registry) as JSON.

Powers the hub 报表血缘 view: for each report yaml, which columns are bound to
which 口径, and where that metric's numbers come from (source tables).

    venv/bin/python hub/scripts/report_bindings_dump.py   # → JSON on stdout
"""
import json
import sys

from semantic.metrics import load_registry
from semantic.metrics.report_bindings import scan_report_bindings


def main() -> int:
    by_id = {m.id: m for m in load_registry()}
    reports: dict[str, dict] = {}
    seen: set[tuple] = set()

    for b in scan_report_bindings():
        # 套餐 / 单品 sheets repeat the same columns — dedupe per (report, column, metric)
        key = (b.report, b.column, b.metric_id)
        if key in seen:
            continue
        seen.add(key)
        m = by_id.get(b.metric_id)
        rep = reports.setdefault(b.report, {"report": b.report, "columns": []})
        rep["columns"].append({
            "column": b.column,
            "metricId": b.metric_id,
            "metricName": m.name if m else None,
            "domain": m.domain if m else None,
            "confidence": m.confidence if m else None,
            "formula": m.formula.business if m else None,
            "sourceTables": m.lineage.source_tables if m else [],
            "found": m is not None,
        })

    json.dump({"reports": list(reports.values())}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
