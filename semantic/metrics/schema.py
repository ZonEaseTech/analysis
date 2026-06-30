"""Metric registry schema — the structured spec for one 口径 (metric).

This is the single machine-readable shape every metric definition must take.
Vocabulary borrows from the metrics-as-code standards we benchmarked
(dbt Semantic Layer / MetricFlow `metric`, Cube measure, ODCS quality block,
OpenLineage source lineage) but stays a lightweight in-house dataclass — no
query engine, no new runtime deps. See docs/plan/2026-06-15-metrics-registry.md.

Downstream consumers:
  - render_catalog.py  → generates docs/metrics-catalog.md (kills doc drift)
  - hub `metrics` API  → renders the 口径中心 page from structured data
  - report yaml `metric:` bindings → pull the 口径 comment by id
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ── Controlled vocabularies ──────────────────────────────────────────
# domain = business scenario the metric belongs to (按业务场景模块化).
DOMAINS: dict[str, str] = {
    "sales": "销售域",
    "settlement": "结算域",
    "finance": "财务域",
    "kpi": "KPI 比率",
    "metadata": "元数据 / 审计",
}

# confidence = how trustworthy the number is right now.
CONFIDENCE: dict[str, str] = {
    "ACTUAL": "真值（已对账 / 直接取数）",
    "ESTIMATED": "估算（用默认率 / 待事实数据升真值）",
    "NA": "暂不可用（依赖未接入的数据源）",
}

# status = lifecycle of the metric.
STATUS: dict[str, str] = {
    "live": "已上线",
    "estimated": "已上线（估算口径）",
    "planned": "待接入",
    "audit-meta": "审计元数据列",
}


@dataclass
class Formula:
    """How the metric is computed, in the three forms it currently exists in.

    business: human-readable formula, e.g. "堂食 GMV + 外卖 GMV".
    sql_refs: where the SQL/Python actually lives, as "path:line — snippet".
    excel:    the Excel formula a report encodes, if any (e.g. "=AJ/H").
    """
    business: str
    sql_refs: list[str] = field(default_factory=list)
    excel: Optional[str] = None


@dataclass
class Lineage:
    """Source provenance (可追溯取值口径).

    source_tables:    physical ttpos / external tables consumed.
    upstream_metrics: ids of metrics this one is composed from (referential).
    """
    source_tables: list[str] = field(default_factory=list)
    upstream_metrics: list[str] = field(default_factory=list)


@dataclass
class Reconciliation:
    """The对账锚 that proves the number ties out (ODCS quality-block analogue)."""
    anchor: Optional[str] = None      # what it is checked against
    impl: Optional[str] = None        # path:line of the check
    status: Optional[str] = None      # last measured result, e.g. "2026-04 diff 0.0002%"


@dataclass
class Metric:
    """One metric / 口径 — the unit of the registry."""
    id: str
    name: str
    domain: str
    status: str
    confidence: str
    definition: str
    formula: Formula
    lineage: Lineage = field(default_factory=Lineage)
    grain: Optional[str] = None
    unit: Optional[str] = None
    reconciliation: Optional[Reconciliation] = None
    industry_benchmark: Optional[str] = None
    current_value: Optional[str] = None
    report_display: Optional[str] = None
    notes: Optional[str] = None
    related_docs: list[str] = field(default_factory=list)

    @property
    def anchor(self) -> str:
        """Stable markdown anchor for catalog deep-links (== heading slug)."""
        return self.id.replace("_", "-")


# ── Construction + validation ────────────────────────────────────────

def metric_from_dict(d: dict) -> Metric:
    """Build a Metric from a raw yaml dict, with shape validation.

    Raises ValueError with a precise message on any schema violation so a
    typo in the registry fails loud at load time, not silently downstream.
    """
    mid = d.get("id")
    if not mid or not isinstance(mid, str):
        raise ValueError(f"metric missing string 'id': {d!r:.120}")
    where = f"metric {mid!r}"

    for required in ("name", "domain", "status", "confidence", "definition"):
        if not d.get(required):
            raise ValueError(f"{where} missing required field '{required}'")

    if d["domain"] not in DOMAINS:
        raise ValueError(
            f"{where} has unknown domain {d['domain']!r}; allowed: {sorted(DOMAINS)}"
        )
    if d["confidence"] not in CONFIDENCE:
        raise ValueError(
            f"{where} has unknown confidence {d['confidence']!r}; allowed: {sorted(CONFIDENCE)}"
        )
    if d["status"] not in STATUS:
        raise ValueError(
            f"{where} has unknown status {d['status']!r}; allowed: {sorted(STATUS)}"
        )

    fraw = d.get("formula")
    if not isinstance(fraw, dict) or not fraw.get("business"):
        raise ValueError(f"{where} needs formula.business")
    formula = Formula(
        business=fraw["business"],
        sql_refs=list(fraw.get("sql_refs") or []),
        excel=fraw.get("excel"),
    )

    lraw = d.get("lineage") or {}
    lineage = Lineage(
        source_tables=list(lraw.get("source_tables") or []),
        upstream_metrics=list(lraw.get("upstream_metrics") or []),
    )

    recon = None
    rraw = d.get("reconciliation")
    if rraw:
        recon = Reconciliation(
            anchor=rraw.get("anchor"),
            impl=rraw.get("impl"),
            status=rraw.get("status"),
        )

    return Metric(
        id=mid,
        name=d["name"],
        domain=d["domain"],
        status=d["status"],
        confidence=d["confidence"],
        definition=d["definition"],
        formula=formula,
        lineage=lineage,
        grain=d.get("grain"),
        unit=d.get("unit"),
        reconciliation=recon,
        industry_benchmark=d.get("industry_benchmark"),
        current_value=d.get("current_value"),
        report_display=d.get("report_display"),
        notes=d.get("notes"),
        related_docs=list(d.get("related_docs") or []),
    )


def validate_registry(metrics: list[Metric]) -> None:
    """Cross-metric integrity: unique ids + upstream_metrics references resolve.

    This is the contract check — a metric that claims to be composed from
    `gmv` must point at a `gmv` that exists. Raises ValueError on any break.
    """
    ids = [m.id for m in metrics]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate metric ids in registry: {sorted(dupes)}")

    known = set(ids)
    for m in metrics:
        for up in m.lineage.upstream_metrics:
            if up not in known:
                raise ValueError(
                    f"metric {m.id!r} references unknown upstream metric {up!r}; "
                    f"known ids: {sorted(known)}"
                )
