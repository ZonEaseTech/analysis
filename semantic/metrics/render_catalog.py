"""Generate docs/metrics-catalog.md from the metric registry.

This kills the doc-vs-code drift: the catalog markdown is no longer
hand-maintained — it is a pure projection of semantic/metrics/registry/*.yaml.

Run after editing any registry yaml:

    venv/bin/python -m semantic.metrics.render_catalog          # write the file
    venv/bin/python -m semantic.metrics.render_catalog --check  # CI: fail if stale

The `--check` mode is the mechanical replacement for the old "改口径=改文档"
discipline rule: CI diffs the freshly-rendered output against the committed
file and fails if they differ.
"""
from __future__ import annotations

import os
import sys

from .loader import load_registry, registry_by_domain
from .schema import CONFIDENCE, DOMAINS, STATUS, Metric

_HERE = os.path.dirname(__file__)
REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
CATALOG_PATH = os.path.join(REPO_ROOT, "docs", "metrics-catalog.md")

_HEADER = """\
# 口径地图 — Metrics Catalog

> ⚠️ **本文件由 `semantic/metrics/registry/*.yaml` 自动生成**
> (`semantic/metrics/render_catalog.py`)。**不要手改本文件。**
> 改口径 = 改 registry yaml → 跑 `venv/bin/python -m semantic.metrics.render_catalog`。
>
> 每个核心指标的唯一真源：业务含义 / 公式 / SQL / 来源 / 对账锚 / 置信度。
> 客户/财务/老板问"这个数怎么算的"，先翻这里。
> 业界对应：dbt Semantic Layer / Cube / DataHub / ODCS。
"""

_FOOTER = """\
## 排障速查

| 症状 | 看哪里 |
|---|---|
| 某 SKU 单份成本异常高（>500） | BOM 来源（看是否 fallback 误匹配） |
| 某 SKU 单份成本 = 0 | 价来源 = "无 (strict)"，要客户在成本表里维护该物料 |
| 总成本变化 > 20% | 跑 `git log` 看 BOM/price 配置改动；跑 `tests/test_resolver_parity.py` |
| BQ vs ttpos 差 > 0.001% | 看是否启用 merchant_charge_fee/merchant_discount |
| 月度毛利大幅波动 | 跑 `pnl_statement --compare-with` 看 Sheet 7 量/价/成本/结构归因 |
| 客户问"某指标怎么算" | 翻本文档对应章节 → 给 file:line 引用 |
| 数据缺失 (SOURCE_COVERAGE 报警) | console 输出 `[bom=无 | price=无]` 直接指源；联系客户补维护 |

## 相关文档

- [pnl-statement-design.md](./pnl-statement-design.md) — P&L 设计稿
- [pnl-primer-for-engineers.md](./pnl-primer-for-engineers.md) — 工程师视角财务 P&L 入门
- [pnl-accounting-standards-gap.md](./pnl-accounting-standards-gap.md) — 会计准则差异 / 财务对接
- [profit-margin-reconciliation-checklist.md](./profit-margin-reconciliation-checklist.md) — F vs G 22 类对账因素
- [profit-report-takeout-semantics.md](./profit-report-takeout-semantics.md) — 外卖口径调研归档
- [architecture-evolution-roadmap.md](./architecture-evolution-roadmap.md) — 整体演进路线
- [.claude/skills/onboard-fact-table/SKILL.md](../.claude/skills/onboard-fact-table/SKILL.md) — 接入新事实表 skill

## 维护规则

**改口径 = 改 `semantic/metrics/registry/*.yaml`**，然后跑
`venv/bin/python -m semantic.metrics.render_catalog` 重新生成本文件。
代码改了忘改 registry → 文档变谎言。强制 review：PR 改
`semantic/entities/*.py` / `semantic/aggregations/pnl_layers.py` 的，
必须同步改对应 registry 条目，并重新生成本文件（CI `--check` 会拦）。
"""


def _toc(metrics: list[Metric]) -> str:
    """速查目录 — one row per domain, linking each metric by stable anchor."""
    grouped = registry_by_domain(metrics)
    lines = ["## 速查目录", "", "| 业务域 | 指标 |", "|---|---|"]
    for domain, label in DOMAINS.items():
        items = grouped.get(domain) or []
        if not items:
            continue
        links = " · ".join(f"[{m.name}](#{m.anchor})" for m in items)
        lines.append(f"| {label} | {links} |")
    return "\n".join(lines)


def _kv(label: str, value: str) -> str:
    return f"**{label}**：{value}"


def _render_metric(m: Metric) -> str:
    out: list[str] = []
    # explicit anchor so TOC links are deterministic regardless of heading text
    out.append(f'<a id="{m.anchor}"></a>')
    out.append(f"## {m.name}")
    out.append("")

    badges = (
        f"`{DOMAINS[m.domain]}` · "
        f"状态 `{STATUS[m.status]}` · "
        f"置信度 `{CONFIDENCE[m.confidence]}`"
    )
    if m.grain:
        badges += f" · 粒度 `{m.grain}`"
    if m.unit:
        badges += f" · 单位 `{m.unit}`"
    out.append(badges)
    out.append("")

    out.append(_kv("业务含义", m.definition))
    out.append("")
    out.append(_kv("公式", f"`{m.formula.business}`"))
    if m.formula.excel:
        out.append("")
        out.append(_kv("Excel", f"`{m.formula.excel}`"))
    if m.formula.sql_refs:
        out.append("")
        out.append("**SQL 实现**：")
        for ref in m.formula.sql_refs:
            out.append(f"- `{ref}`")

    lin = m.lineage
    if lin.source_tables or lin.upstream_metrics:
        out.append("")
        parts = []
        if lin.source_tables:
            parts.append("源表 " + ", ".join(f"`{t}`" for t in lin.source_tables))
        if lin.upstream_metrics:
            ups = ", ".join(f"[{u}](#{u.replace('_', '-')})" for u in lin.upstream_metrics)
            parts.append("上游指标 " + ups)
        out.append(_kv("数据来源", " · ".join(parts)))

    if m.reconciliation:
        r = m.reconciliation
        rec = r.anchor or ""
        if r.impl:
            rec += f"（impl: `{r.impl}`）"
        if r.status:
            rec += f" — {r.status}"
        out.append("")
        out.append(_kv("对账锚", rec))

    if m.industry_benchmark:
        out.append("")
        out.append(_kv("行业基准", m.industry_benchmark))
    if m.current_value:
        out.append("")
        out.append(_kv("当前实测", m.current_value))
    if m.report_display:
        out.append("")
        out.append(_kv("报表展示", m.report_display))
    if m.notes:
        out.append("")
        out.append(_kv("注意 / 排障", m.notes))
    if m.related_docs:
        out.append("")
        links = ", ".join(f"[{d}](./{d})" for d in m.related_docs)
        out.append(_kv("相关文档", links))

    return "\n".join(out)


def render_catalog(metrics: list[Metric] | None = None) -> str:
    """Return the full metrics-catalog.md content as a string."""
    metrics = metrics if metrics is not None else load_registry()
    blocks = [_HEADER, _toc(metrics), "---"]
    for m in metrics:
        blocks.append(_render_metric(m))
        blocks.append("---")
    blocks.append(_FOOTER)
    return "\n\n".join(blocks).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    content = render_catalog()
    if "--check" in argv:
        existing = ""
        if os.path.exists(CATALOG_PATH):
            with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                existing = f.read()
        if existing != content:
            print(
                "❌ docs/metrics-catalog.md is stale — registry changed but the "
                "catalog was not regenerated.\n"
                "   Run: venv/bin/python -m semantic.metrics.render_catalog",
                file=sys.stderr,
            )
            return 1
        print("✅ metrics-catalog.md is in sync with the registry.")
        return 0

    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✅ wrote {CATALOG_PATH} ({len(content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
