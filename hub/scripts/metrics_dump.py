"""Dump the metric registry as JSON for the hub 口径中心 page.

Run by the hub backend via venv/bin/python (PYTHONPATH=repo root), mirroring
how reports preview uses xlsx_preview.py. Keeps the registry the single
source of truth — the hub never re-parses the yaml, it asks Python.

    venv/bin/python hub/scripts/metrics_dump.py   # → JSON on stdout

Output (camelCase for the TS frontend):
    { "domains": [ { "key", "label", "metrics": [ <metric>, ... ] } ] }
"""
import json
import sys

from semantic.metrics import DOMAINS, CONFIDENCE, STATUS, load_registry
from semantic.metrics.loader import registry_by_domain


def metric_to_json(m) -> dict:
    return {
        "id": m.id,
        "anchor": m.anchor,
        "name": m.name,
        "domain": m.domain,
        "status": m.status,
        "statusLabel": STATUS.get(m.status, m.status),
        "confidence": m.confidence,
        "confidenceLabel": CONFIDENCE.get(m.confidence, m.confidence),
        "definition": m.definition,
        "grain": m.grain,
        "unit": m.unit,
        "formula": {
            "business": m.formula.business,
            "sqlRefs": m.formula.sql_refs,
            "excel": m.formula.excel,
        },
        "lineage": {
            "sourceTables": m.lineage.source_tables,
            "upstreamMetrics": m.lineage.upstream_metrics,
        },
        "reconciliation": (
            {
                "anchor": m.reconciliation.anchor,
                "impl": m.reconciliation.impl,
                "status": m.reconciliation.status,
            }
            if m.reconciliation
            else None
        ),
        "industryBenchmark": m.industry_benchmark,
        "currentValue": m.current_value,
        "reportDisplay": m.report_display,
        "notes": m.notes,
        "relatedDocs": m.related_docs,
    }


def main() -> int:
    metrics = load_registry()
    grouped = registry_by_domain(metrics)
    domains = [
        {
            "key": key,
            "label": label,
            "metrics": [metric_to_json(m) for m in grouped.get(key, [])],
        }
        for key, label in DOMAINS.items()
        if grouped.get(key)
    ]
    json.dump({"domains": domains}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
