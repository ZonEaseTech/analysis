#!/usr/bin/env python3
"""财务标准 P&L 损益表导出 — P3.5 主入口.

按"业界标准 P&L 5 层结构"输出, 给老板/财务看. 跟 profit_margin / profit_by_price
是互补关系:
  - profit_margin       店长/采购视角 (SKU × BOM 物料 维度看利润)
  - profit_by_price     营销视角 (SKU × 价格档 维度看价格策略)
  - pnl_statement (本)  财务/经营视角 (集团 / 按 P&L 分层结构)

Excel 输出 (Phase 1 完整版):
  Sheet 1: 集团损益表 (P&L Statement, 行级分层 + MoM + % Net Sales)
  Sheet 2: KPI Dashboard (Gross Margin / Food Cost / Prime Cost / AOV /
                          Channel Mix + 行业基准对比 + 健康度评级)

数据源 (Phase 1 范围):
  - 销售域: sale_event entity (堂食 + 外卖 UNION ALL, 带 channel)
  - 物料成本: profit_margin 的 BOM × 销量 (复用)
  - 平台抽佣: P3 fact_overrides Resolver (估算)
  - 第 4 层固定成本 (房租/人力/水电/营销): 暂 N/A, 待 Phase 3

实施分两段:
  P3.5b (本): 主流程骨架 + Excel writer + smoke test (mock 数据)
  P3.5c (下): 接 BQ 实际拉数 (sale_event + COGS), 端到端跑通真月份

CLI:
  venv/bin/python -m bq_reports.pnl_statement --month 2026-04 \\
      --output exports/pnl_202604.xlsx
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

from bq_reports.utils.bq_client import setup_proxy
from semantic.aggregations.kpi_ratios import Kpi, compute_kpis
from semantic.aggregations.pnl_layers import PnlStatement, build_pnl
from semantic.comparison import compute_mom_changes, compute_yoy_changes
from semantic.dimensions.time import month_to_ts_range, assert_month_not_frozen
from semantic.entities import sale_event
from semantic.resolvers import Resolver, load_resolvers_from_yaml
from utils.report_engine import ReportEngine


# ───────────────────────────────────────────────────────────────
# BQ 拉数 (P3.5c)
# ───────────────────────────────────────────────────────────────

# sale_event 已按 (item_uuid, price, channel) 自然分粒度. 我们 JOIN product_package
# 拿 item_name + product_type, 让 COGS 计算能区分套餐/单品并应用 fallback_bom 覆盖.
# Python 后续做双重聚合: (a) channel-level for aggregate_sales_by_channel,
# (b) (item, channel) for COGS. 一次 BQ 查询, 省费.
def _build_pnl_sales_sql(exclude_test_business: bool = False) -> str:
    return f"""
WITH {sale_event.sale_event_cte(exclude_test_business=exclude_test_business)}
SELECT
  se.item_uuid,
  REGEXP_REPLACE(COALESCE(
    JSON_EXTRACT_SCALAR(pp.name, '$.zh'),
    JSON_EXTRACT_SCALAR(pp.name, '$.en'),
    '未知'
  ), r'^\\s+|\\s+$', '') AS item_name,
  IFNULL(pp.product_type, 0) AS product_type,
  se.price, se.channel, se.qty, se.sales_price, se.gross_amount, se.original_amount, se.actual_amount,
  se.refund_qty, se.refund_amount, se.free_qty, se.give_qty,
  se.free_amount, se.give_amount, se.discount_amount,
  se.cancelled_qty, se.cancelled_amount
FROM sale_event se
LEFT JOIN `{{project}}`.`{{dataset}}`.`ttpos_product_package` pp
  ON pp.uuid = se.item_uuid
"""


_PNL_SALES_SQL = _build_pnl_sales_sql(exclude_test_business=False)
_PNL_SALES_SQL_TB = _build_pnl_sales_sql(exclude_test_business=True)


def _fetch_ttpos_net_sales(engine, merchants, start_ts, end_ts) -> float:
    """跑 ttpos 后端等价 SQL 拿 Net Sales (P4 anchor check 用).

    Returns: 全集团 ttpos 口径实收金额合计.
    """
    from semantic.reconciliation import TTPOS_NET_SALES_SQL

    raw_rows, _ = engine.query(
        sql_template=TTPOS_NET_SALES_SQL,
        merchants=merchants,
        start_ts=start_ts,
        end_ts=end_ts,
        workers=10,
        label="ttpos anchor",
    )
    return sum(float(row.ttpos_net_sales or 0) for row in raw_rows)


def _fetch_pnl_sales_rows(engine, merchants, start_ts, end_ts):
    """全集团并发拉数 → sale_event (item × price × channel) 全量行.

    每行附带 store_num / store_name (由 row_proxy_factory 注入).
    返回的 rows 直接喂给 aggregate_sales_by_channel + _compute_cogs_from_rows.
    对启用测试营业开关的店, 按店切换到带过滤的 SQL (对齐 ttpos 后台口径).
    """
    from semantic.dimensions.test_business import get_stores_with_test_business
    tb_stores = get_stores_with_test_business(
        engine.client, [m[1] for m in merchants])
    sql_factory = (
        (lambda u: _PNL_SALES_SQL_TB if u in tb_stores else _PNL_SALES_SQL)
        if tb_stores else None
    )

    raw_rows, errors = engine.query(
        sql_template=_PNL_SALES_SQL,
        merchants=merchants,
        start_ts=start_ts,
        end_ts=end_ts,
        workers=10,
        row_proxy_factory=lambda row, acc, num, name: type("RowProxy", (), {
            "__getattr__": lambda self, attr: getattr(row, attr),
            "account": acc, "store_num": num, "store_name": name,
        })(),
        label="P&L 销售",
        sql_template_factory=sql_factory,
    )
    if errors:
        print(f"[警告] {len(errors)} 店查询失败 (可能没 takeout 表)")
    return raw_rows


def _compute_cogs_per_store(
    rows,
    bom_data,
    combo_structure,
    *,
    uploaded_prices,
    erp_prices,
    price_layers,
    bom_layers,
    strict_price,
):
    """跟 _compute_cogs_from_rows 同算法, 但按 store_num 分组返回.

    Returns: {store_num: {dine, takeout, total}}
    """
    from collections import defaultdict

    # 按 store 分组 rows
    by_store: dict = defaultdict(list)
    for row in rows:
        by_store[row.store_num].append(row)

    out = {}
    for store_num, store_rows in by_store.items():
        out[store_num] = _compute_cogs_from_rows(
            store_rows, bom_data, combo_structure,
            uploaded_prices=uploaded_prices,
            erp_prices=erp_prices,
            price_layers=price_layers,
            bom_layers=bom_layers,
            strict_price=strict_price,
        )
    return out


def _compute_cogs_from_rows(
    rows,
    bom_data,
    combo_structure,
    *,
    uploaded_prices,
    erp_prices,
    price_layers,
    bom_layers,
    strict_price,
):
    """从 (item × channel × qty) 行计算 COGS, 按 channel 拆.

    跟 profit_margin.aggregate_with_bom / _build_summary_rows 的 cost 算法**完全
    等价**, 复用同一份 BOM 数据 + Resolver. 关键差异:
      - profit_margin 按 (store, item_uuid) 聚合, channel 信息丢了
      - pnl_statement 在 (store, item_uuid, channel) 粒度聚合, 然后按 channel 拆 cogs

    完整支持:
      - 单品 (product_type=0): 直接走 item_bom + 单价 Resolver
      - 套餐 (product_type=1): 遍历 combo_structure 子商品, BOM 摊薄到 (bom_num ×
        child_num × weight), 跟 profit_margin combo 模式一致
      - fallback_bom 覆盖 (bom_layers): _match_bom_layered 命中即 override BQ 原生
      - Resolver 单价 (price_layers / uploaded / ERPNext / strict)
    """
    # 延迟 import 避免循环依赖
    from bq_reports.profit_margin_report import (
        _match_bom_layered,
        _resolve_unit_price_with_source,
    )

    # 步骤 1: 按 (store, item_uuid) 聚合每 channel 的 qty + 元数据
    from collections import defaultdict
    by_item: dict = defaultdict(lambda: {
        "dine": 0.0, "takeout": 0.0,
        "name": "", "product_type": 0,
    })
    for row in rows:
        store_num = row.store_num
        item_uuid = str(row.item_uuid)
        channel = row.channel
        if channel not in ("dine", "takeout"):
            continue
        key = (store_num, item_uuid)
        entry = by_item[key]
        entry[channel] += float(row.qty or 0)
        if not entry["name"]:
            entry["name"] = row.item_name or ""
        entry["product_type"] = int(row.product_type or 0)

    # 步骤 2: 每个 (store, item) 算 per-unit cost (单份成本), 然后 × channel qty
    cogs = {"dine": 0.0, "takeout": 0.0}
    for (store_num, item_uuid), entry in by_item.items():
        dine_qty = entry["dine"]
        takeout_qty = entry["takeout"]
        if dine_qty + takeout_qty <= 0:
            continue
        item_name = entry["name"]
        is_combo = entry["product_type"] == 1

        store_boms = bom_data.get(store_num, {})

        # 2a) 先尝试 fallback_bom 匹配 (bom_layers)
        matched, _layer_name = _match_bom_layered(item_name, bom_layers)
        if matched:
            # fallback_bom 命中, 用匹配到的 BOM (替换 BQ 原生)
            bom_list = []
            for code, name, bom_num, uom in matched:
                base_price, _src = _resolve_unit_price_with_source(
                    code, 0, uploaded_prices, erp_prices,
                    price_layers=price_layers, strict=strict_price,
                    material_name=name,
                )
                bom_list.append((code, bom_num, base_price))
        elif is_combo:
            # 2b) 套餐: 遍历 combo_structure 子商品, BOM 摊薄
            store_struct = combo_structure.get(store_num, {})
            child_specs = store_struct.get(item_uuid, [])
            bom_list_dict: dict = {}  # 同物料跨子商品累加
            for spec in child_specs:
                if isinstance(spec, (tuple, list)) and len(spec) == 3:
                    child_uuid, child_num, weight = spec
                else:
                    child_uuid, child_num, weight = spec, 1.0, 1.0
                child_mult = float(child_num) * float(weight)
                child_bom = store_boms.get(str(child_uuid), [])
                for material_code, _n, bom_num, _u, conv_rate, bq_price in child_bom:
                    if not material_code:
                        continue
                    base_price, _src = _resolve_unit_price_with_source(
                        material_code, bq_price,
                        uploaded_prices, erp_prices,
                        price_layers=price_layers, strict=strict_price,
                    )
                    unit_price = base_price * (conv_rate or 1)
                    weighted_num = bom_num * child_mult
                    if material_code in bom_list_dict:
                        prev_num, prev_price = bom_list_dict[material_code]
                        bom_list_dict[material_code] = (
                            prev_num + weighted_num, unit_price,
                        )
                    else:
                        bom_list_dict[material_code] = (weighted_num, unit_price)
            bom_list = [
                (code, num, price)
                for code, (num, price) in bom_list_dict.items()
            ]
        else:
            # 2c) 单品: 直接走 item_bom
            item_bom = store_boms.get(item_uuid, [])
            bom_list = []
            seen = set()
            for material_code, _n, bom_num, _u, conv_rate, bq_price in item_bom:
                if not material_code or material_code in seen:
                    continue
                seen.add(material_code)
                base_price, _src = _resolve_unit_price_with_source(
                    material_code, bq_price,
                    uploaded_prices, erp_prices,
                    price_layers=price_layers, strict=strict_price,
                )
                unit_price = base_price * (conv_rate or 1)
                bom_list.append((material_code, bom_num, unit_price))

        # 步骤 3: 单份成本 → 按 channel × qty 累加
        per_unit_cost = sum(num * price for _code, num, price in bom_list)
        cogs["dine"] += per_unit_cost * dine_qty
        cogs["takeout"] += per_unit_cost * takeout_qty

    return {
        "dine": cogs["dine"],
        "takeout": cogs["takeout"],
        "total": cogs["dine"] + cogs["takeout"],
    }


# ───────────────────────────────────────────────────────────────
# 销售域聚合 (按 channel 拆出 dine_/takeout_ 字段)
# ───────────────────────────────────────────────────────────────

# 跨 channel 的总数 (build_pnl 用)
_TOTAL_FIELDS = (
    "qty", "sales_price", "actual_amount",
    "refund_amount", "cancelled_amount",
    "free_amount", "give_amount", "discount_amount",
    "order_count",
)

# 按 channel 拆出来的字段 (build_pnl 用 dine_/takeout_sales_price + qty)
_PER_CHANNEL_FIELDS = ("qty", "sales_price")


def aggregate_sales_by_channel(rows: Iterable[Any]) -> dict:
    """从 sale_event-like 行 (含 channel ∈ {'dine','takeout'}) 聚合成单 dict.

    输出 dict 满足 build_pnl(sales_rows=[dict]) 的字段需求:
      - 总数字段: qty / sales_price / actual_amount / ... (跨 channel 求和)
      - 渠道拆分: dine_qty / dine_sales_price / takeout_qty / takeout_sales_price

    输入 row 字段 (缺则按 0 算):
      channel: 'dine' | 'takeout'
      qty, sales_price, actual_amount, refund_amount, cancelled_amount,
      free_amount, give_amount, discount_amount, order_count
    """
    out: dict = defaultdict(float)
    for row in rows:
        # 总数
        for field in _TOTAL_FIELDS:
            out[field] += float(_get(row, field, 0) or 0)
        # 按渠道拆 (只拆 qty/sales_price; actual/refund 等总数本身已经按 channel
        # 在 sale_event 里区分了, 不需要重复拆)
        channel = _get(row, "channel", "")
        if channel in ("dine", "takeout"):
            prefix = f"{channel}_"
            for field in _PER_CHANNEL_FIELDS:
                out[f"{prefix}{field}"] += float(_get(row, field, 0) or 0)
    return dict(out)


def _get(row: Any, key: str, default: Any = None) -> Any:
    if hasattr(row, key):
        return getattr(row, key)
    if isinstance(row, dict):
        return row.get(key, default)
    return default


# ───────────────────────────────────────────────────────────────
# 端到端编排 — sales_rows + cogs + resolver → 完整 artifact
# ───────────────────────────────────────────────────────────────

def _per_store_artifacts(
    sales_rows: Iterable[Any],
    store_cogs_map: dict,
    commission_rate_resolver: Optional[Resolver],
    period: str,
) -> list[dict]:
    """每店分组算一份 PnlStatement (用于 Sheet 3 按店损益).

    Args:
        sales_rows: 全集团 sale_event 行 (每行带 store_num)
        store_cogs_map: {store_num: {dine, takeout, total}} — 由 _compute_cogs_per_store
            预先算好
        commission_rate_resolver: 跟主流程同一个 Resolver
        period: 期间标识 (写入每个 PnlStatement)

    Returns: [{store_num, store_name, pnl, kpis, channel_split}, ...]
    """
    from collections import defaultdict

    # 按 store 分组 sales_rows
    by_store: dict = defaultdict(list)
    store_names: dict = {}
    for row in sales_rows:
        by_store[row.store_num].append(row)
        if row.store_num not in store_names:
            store_names[row.store_num] = row.store_name

    out = []
    for store_num, rows in sorted(by_store.items()):
        store_cogs = store_cogs_map.get(store_num, {"dine": 0, "takeout": 0, "total": 0})
        artifact = build_pnl_artifact(
            period=period,
            scope=f"店 {store_num}",
            sales_rows=rows,
            cogs_data=store_cogs,
            commission_rate_resolver=commission_rate_resolver,
        )
        out.append({
            "store_num": store_num,
            "store_name": store_names.get(store_num, "-"),
            "artifact": artifact,
        })
    return out


def build_pnl_artifact(
    *,
    period: str,
    scope: str,
    sales_rows: Iterable[Any],
    cogs_data: Optional[dict] = None,
    commission_rate_resolver: Optional[Resolver] = None,
    previous_pnl: Optional[PnlStatement] = None,
    year_ago_pnl: Optional[PnlStatement] = None,
) -> dict:
    """端到端: 输入数据 → PnlStatement + KPIs + MoM/YoY artifact.

    Args:
        period: "2026-04"
        scope: "全集团" / store_num
        sales_rows: sale_event-like rows (含 channel)
        cogs_data: {"total": ..., "dine": ..., "takeout": ...} 或 None
        commission_rate_resolver: P3 Resolver (commission_rate 类别) 或 None
        previous_pnl: 上月 PnlStatement (MoM 用) 或 None
        year_ago_pnl: 去年同月 (YoY 用) 或 None

    Returns: {
        'pnl': PnlStatement,
        'kpis': list[Kpi],
        'mom': dict[code, PeriodChange],
        'yoy': dict[code, PeriodChange],
        'sales_totals': dict,
    }
    """
    aggregated = aggregate_sales_by_channel(sales_rows)

    pnl = build_pnl(
        period=period,
        scope=scope,
        sales_rows=[aggregated],
        cogs_data=cogs_data,
        commission_rate_resolver=commission_rate_resolver,
    )

    kpis = compute_kpis(
        pnl_amounts=pnl.all_amounts(),
        sales_totals=aggregated,
    )

    mom = compute_mom_changes(
        pnl.all_amounts(),
        previous_pnl.all_amounts() if previous_pnl else None,
    )
    yoy = compute_yoy_changes(
        pnl.all_amounts(),
        year_ago_pnl.all_amounts() if year_ago_pnl else None,
    )

    return {
        "pnl": pnl,
        "kpis": kpis,
        "mom": mom,
        "yoy": yoy,
        "sales_totals": aggregated,
    }


# ───────────────────────────────────────────────────────────────
# Excel writer — 财务化格式
# ───────────────────────────────────────────────────────────────

# 财务化数字格式 (xlsxwriter 风格)
# `#,##0;(#,##0);"—"` = 正数千分位 / 负数括号 / 零显示破折号
_FMT_AMOUNT = '#,##0;(#,##0);"—"'
_FMT_PCT = '0.0%;-0.0%;"—"'
_FMT_PCT_MOM = '+0.0%;-0.0%;"—"'

# 健康度颜色 (Excel conditional bg)
_HEALTH_COLORS = {
    "healthy":    ("#C6EFCE", "#006100"),
    "acceptable": (None, None),
    "warning":    ("#FFEB9C", "#9C5700"),
    "critical":   ("#FFC7CE", "#9C0006"),
    "n/a":        (None, "#999999"),
}


def write_pnl_excel(
    artifact: dict,
    output_path: str,
    *,
    per_store_artifacts: Optional[list] = None,
    channel_data: Optional[dict] = None,
    menu_rows: Optional[list] = None,
    previous_artifact: Optional[dict] = None,
    force: bool = False,
    sales_check_rows: Optional[list] = None,
):
    """写 P&L Excel — 多 sheet, 支持 drill-down.

    Sheet 1: 集团损益表 (P&L Statement)              — 总览
    Sheet 2: KPI Dashboard                          — 健康度
    Sheet 3: 按店损益 (per_store_artifacts 非空时)    — 哪家店赚/亏
    Sheet 4: 按渠道对比 (channel_data 非空时)          — 堂食 vs 外卖
    Sheet 5: 菜单工程矩阵 (menu_rows 非空时)           — Stars/Plowhorses/Puzzles/Dogs
    Sheet 6: 数据来源审计                            — 每个数字怎么来的
    Sheet 7: 跨期差异分解 (previous_artifact 非空时)   — 量/价/成本/结构 4 维归因

    Args:
        artifact: build_pnl_artifact 主输出
        per_store_artifacts: _per_store_artifacts 输出
        channel_data: {dine: {...}, takeout: {...}}
        menu_rows: _compute_menu_engineering 输出
        previous_artifact: 上期 build_pnl_artifact (Sheet 7 用)
    """
    import xlsxwriter
    import statistics

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(output_path))
    try:
        _write_pnl_main_sheet(wb, artifact)
        _write_kpi_dashboard_sheet(wb, artifact)
        if per_store_artifacts:
            _write_per_store_sheet(wb, per_store_artifacts)
        if channel_data:
            _write_channel_compare_sheet(wb, artifact, channel_data)
        if menu_rows:
            qty_median = statistics.median(r["qty"] for r in menu_rows) if menu_rows else 0
            margin_median = statistics.median(r["margin"] for r in menu_rows) if menu_rows else 0
            _write_menu_engineering_sheet(wb, menu_rows, qty_median, margin_median)
        _write_source_audit_sheet(wb, artifact)
        if previous_artifact:
            _write_variance_sheet(wb, artifact, previous_artifact)

        # 零容差闸门 (在 wb.close() 前执行, 有 MUST_FIX 且无 --force 则 exit(2))
        # 店粒度销售恒等式 (DEFAULT_IDENTITIES); P&L 层金额 (COGS/费用) 是估算量不参与 — 技术债⑤已还 (PR-B Task 5)
        from semantic.validators.gate import (
            add_watermark_sheet_xlsxwriter, validate_and_gate)
        from semantic.validators.identities import DEFAULT_IDENTITIES

        # sales_check_rows: 店粒度销售桶聚合行 (由调用方从 sales_rows 构建并传入)
        # 若未传入 (单元测试/mock 场景), 回退到非空闸门 (identities=[])
        if sales_check_rows:
            gate_rows = sales_check_rows
            gate_identities = DEFAULT_IDENTITIES
        else:
            gate_rows = [{"_pnl": True}] if artifact.get("pnl") else []
            gate_identities = []
        outcome = validate_and_gate(
            gate_rows, identities=gate_identities,
            force=force, report_name="pnl_statement",
            row_label=lambda r: f"店 {r['store_num']}",
        )
        if outcome.needs_watermark:
            add_watermark_sheet_xlsxwriter(wb, outcome.watermark_lines())
    finally:
        wb.close()


def _write_pnl_main_sheet(wb, artifact: dict):
    """Sheet 1: 集团损益表 — 行级 P&L 分层 + MoM 列.

    财务化格式:
      - 千分位 / 负数括号
      - 关键节点 (subtotal) 加粗 + 上方分隔线
      - 减项缩进 1-2 层
      - N/A 显式标 (灰斜体)
    """
    pnl: PnlStatement = artifact["pnl"]
    mom = artifact["mom"]

    ws = wb.add_worksheet("集团损益表")

    # ── Format 缓存 ──
    fmt_header = wb.add_format({
        "bold": True, "bg_color": "#4472C4", "font_color": "white",
        "align": "center", "border": 1,
    })
    fmt_meta = wb.add_format({"italic": True, "font_color": "#666666"})
    fmt_subtotal_label = wb.add_format({"bold": True, "top": 2})
    fmt_subtotal_amount = wb.add_format(
        {"bold": True, "top": 2, "num_format": _FMT_AMOUNT})
    fmt_subtotal_pct = wb.add_format(
        {"bold": True, "top": 2, "num_format": _FMT_PCT})
    fmt_subtotal_mom = wb.add_format(
        {"bold": True, "top": 2, "num_format": _FMT_PCT_MOM})

    def _label_fmt(indent: int) -> object:
        return wb.add_format({"indent": indent})

    fmt_amount = wb.add_format({"num_format": _FMT_AMOUNT})
    fmt_pct = wb.add_format({"num_format": _FMT_PCT})
    fmt_pct_mom = wb.add_format({"num_format": _FMT_PCT_MOM})
    fmt_na = wb.add_format({"italic": True, "font_color": "#999999",
                            "align": "right"})

    # ── 元信息 (第 1 行)──
    ws.merge_range(
        0, 0, 0, 4,
        f"P&L 损益表  •  期间 {pnl.period}  •  范围 {pnl.scope}",
        wb.add_format({"bold": True, "font_size": 12, "align": "left"}),
    )

    # ── 表头 ──
    headers = ["项目", "金额 (THB)", "% Net Sales", "MoM", "备注 / 状态"]
    for col, h in enumerate(headers):
        ws.write(2, col, h, fmt_header)
    ws.set_column(0, 0, 36)
    ws.set_column(1, 1, 18)
    ws.set_column(2, 2, 14)
    ws.set_column(3, 3, 12)
    ws.set_column(4, 4, 28)

    # Net Sales reference (% Net Sales 分母)
    net_sales_layer = pnl.by_code("net_sales")
    net_sales = net_sales_layer.amount if net_sales_layer else 0

    # ── 写每一层 ──
    for r_idx, layer in enumerate(pnl.layers, start=3):
        is_sub = layer.is_subtotal
        is_na = layer.confidence.value == "n/a"

        # 项目名
        label_fmt = (
            fmt_subtotal_label if is_sub
            else _label_fmt(layer.indent)
        )
        ws.write(r_idx, 0, layer.name_zh, label_fmt)

        # 金额
        if is_na:
            ws.write(r_idx, 1, "N/A", fmt_na)
        else:
            f = fmt_subtotal_amount if is_sub else fmt_amount
            ws.write(r_idx, 1, layer.amount, f)

        # % Net Sales
        if is_na or not net_sales:
            ws.write(r_idx, 2, "N/A" if is_na else "—", fmt_na)
        else:
            f = fmt_subtotal_pct if is_sub else fmt_pct
            ws.write(r_idx, 2, layer.amount / net_sales, f)

        # MoM
        change = mom.get(layer.code)
        if change is None or change.pct_delta is None:
            ws.write(r_idx, 3, "N/A", fmt_na)
        else:
            f = fmt_subtotal_mom if is_sub else fmt_pct_mom
            ws.write(r_idx, 3, change.pct_delta, f)

        # 备注
        notes = []
        if layer.confidence.value == "n/a":
            notes.append("待接入")
        elif layer.confidence.value == "estimated":
            notes.append("估算")
        if layer.note:
            notes.append(layer.note)
        ws.write(r_idx, 4, " / ".join(notes), fmt_meta if notes else fmt_meta)

    ws.freeze_panes(3, 0)

    # 顶部加一行说明
    ws.set_row(1, 18)
    ws.merge_range(
        1, 0, 1, 4,
        "本报表为管理会计 / 经营分析用途，不等同于法定财务报表。N/A 项待接入数据。",
        wb.add_format({"italic": True, "font_color": "#999999"}),
    )


def _write_kpi_dashboard_sheet(wb, artifact: dict):
    """Sheet 2: KPI Dashboard — 比率 + 行业基准 + 健康度评级.

    一眼看出"我们健不健康", 老板/财务核心关注.
    """
    kpis: list[Kpi] = artifact["kpis"]

    ws = wb.add_worksheet("KPI Dashboard")

    fmt_header = wb.add_format({
        "bold": True, "bg_color": "#4472C4", "font_color": "white",
        "align": "center", "border": 1,
    })
    fmt_value_pct = wb.add_format({"num_format": '0.00%;-0.00%;"N/A"'})
    fmt_value_num = wb.add_format({"num_format": '#,##0.00;-#,##0.00;"N/A"'})
    fmt_na = wb.add_format({"italic": True, "font_color": "#999999",
                            "align": "right"})

    # 健康度颜色 format 缓存
    health_fmts = {}
    for h, (bg, fg) in _HEALTH_COLORS.items():
        props = {"align": "center"}
        if bg:
            props["bg_color"] = bg
        if fg:
            props["font_color"] = fg
        if h == "n/a":
            props["italic"] = True
        health_fmts[h] = wb.add_format(props)

    headers = ["指标", "值", "行业基准", "评级", "备注"]
    for col, h in enumerate(headers):
        ws.write(0, col, h, fmt_header)
    ws.set_column(0, 0, 30)
    ws.set_column(1, 1, 14)
    ws.set_column(2, 2, 32)
    ws.set_column(3, 3, 12)
    ws.set_column(4, 4, 32)

    health_label = {
        "healthy":    "✅ 健康",
        "acceptable": "⚪ 可接受",
        "warning":    "🟡 关注",
        "critical":   "🔴 必查",
        "n/a":        "— N/A",
    }

    for r_idx, kpi in enumerate(kpis, start=1):
        ws.write(r_idx, 0, kpi.name_zh)

        # 值
        if kpi.value is None:
            ws.write(r_idx, 1, "N/A", fmt_na)
        else:
            f = fmt_value_pct if kpi.format == "percent" else fmt_value_num
            ws.write(r_idx, 1, kpi.value, f)

        # 行业基准
        if kpi.benchmark:
            ws.write(r_idx, 2, kpi.benchmark.description)

        # 评级 — 只对带 benchmark 的指标显示
        # AOV / Channel Mix 等没"universal 行业基准"的指标, 评级列留空避免
        # 误显示"N/A"让人以为数据缺失
        if kpi.benchmark is not None:
            health = kpi.health.value
            ws.write(r_idx, 3, health_label.get(health, ""),
                     health_fmts.get(health, health_fmts["n/a"]))

        # 备注
        ws.write(r_idx, 4, kpi.note or "")

    ws.freeze_panes(1, 0)


# ───────────────────────────────────────────────────────────────
# Sheet 3: 按店损益 (Per-Store P&L)
# ───────────────────────────────────────────────────────────────

def _write_per_store_sheet(wb, per_store_artifacts: list):
    """每店一行 — 关键 P&L 节点 + Gross Margin% 健康度.

    drill-down 价值: 集团 P&L 总数 ↓ 看哪家店赚 / 哪家店亏 / 哪家店扯后腿.
    """
    ws = wb.add_worksheet("按店损益")

    fmt_header = wb.add_format({
        "bold": True, "bg_color": "#4472C4", "font_color": "white",
        "align": "center", "border": 1,
    })
    fmt_amount = wb.add_format({"num_format": _FMT_AMOUNT})
    fmt_pct = wb.add_format({"num_format": _FMT_PCT})
    fmt_negative_amount = wb.add_format({
        "num_format": _FMT_AMOUNT, "font_color": "#9C0006",
    })

    headers = [
        "店编号", "店名",
        "GMV", "Net Sales", "COGS", "Gross Profit", "Gross Margin %",
        "堂食 GMV", "外卖 GMV", "外卖占比",
    ]
    for col, h in enumerate(headers):
        ws.write(0, col, h, fmt_header)
    ws.set_column(0, 0, 8)
    ws.set_column(1, 1, 30)
    ws.set_column(2, 6, 15)
    ws.set_column(7, 9, 13)

    # 按 Gross Profit 降序排店 — 老板第一眼看赚最多的店
    sortable = []
    for entry in per_store_artifacts:
        pnl = entry["artifact"]["pnl"]
        gp = pnl.by_code("gross_profit").amount
        sortable.append((gp, entry))
    sortable.sort(key=lambda x: -x[0])

    for r_idx, (_, entry) in enumerate(sortable, start=1):
        pnl = entry["artifact"]["pnl"]
        gmv = pnl.by_code("gmv").amount
        net_sales = pnl.by_code("net_sales").amount
        cogs_amt = pnl.by_code("cogs").amount  # negative
        gp = pnl.by_code("gross_profit").amount
        gm = gp / net_sales if net_sales else 0
        dine_gmv = pnl.by_code("dine_gmv").amount
        take_gmv = pnl.by_code("takeout_gmv").amount
        take_ratio = take_gmv / gmv if gmv else 0

        ws.write(r_idx, 0, entry["store_num"])
        ws.write(r_idx, 1, entry["store_name"])
        ws.write(r_idx, 2, gmv, fmt_amount)
        ws.write(r_idx, 3, net_sales, fmt_amount)
        ws.write(r_idx, 4, cogs_amt, fmt_amount)
        # 毛利负值标红
        ws.write(r_idx, 5, gp,
                 fmt_negative_amount if gp < 0 else fmt_amount)
        ws.write(r_idx, 6, gm,
                 fmt_negative_amount if gm < 0 else fmt_pct)
        ws.write(r_idx, 7, dine_gmv, fmt_amount)
        ws.write(r_idx, 8, take_gmv, fmt_amount)
        ws.write(r_idx, 9, take_ratio, fmt_pct)

    ws.freeze_panes(1, 2)  # 冻结表头 + 店编号/店名两列
    ws.autofilter(0, 0, len(sortable), len(headers) - 1)


# ───────────────────────────────────────────────────────────────
# Sheet 4: 按渠道损益对比 (Channel P&L Comparison)
# ───────────────────────────────────────────────────────────────

def _write_channel_compare_sheet(wb, artifact: dict, channel_data: dict):
    """堂食 vs 外卖 vs 合计 三列对比 — P&L 各层金额 + % Net Sales.

    drill-down 价值: 老板最关心的"外卖到底赚不赚钱" 答案直接看这表.
    """
    ws = wb.add_worksheet("按渠道对比")

    fmt_header = wb.add_format({
        "bold": True, "bg_color": "#4472C4", "font_color": "white",
        "align": "center", "border": 1,
    })
    fmt_subtotal = wb.add_format(
        {"bold": True, "top": 2, "num_format": _FMT_AMOUNT})
    fmt_amount = wb.add_format({"num_format": _FMT_AMOUNT})
    fmt_pct = wb.add_format({"num_format": _FMT_PCT})
    fmt_negative_amount = wb.add_format(
        {"num_format": _FMT_AMOUNT, "font_color": "#9C0006"})

    pnl = artifact["pnl"]

    # 表头: 项目 / 堂食 / 外卖 / 合计 / 外卖占比
    headers = ["项目", "堂食", "外卖", "合计", "外卖占比"]
    for col, h in enumerate(headers):
        ws.write(0, col, h, fmt_header)
    ws.set_column(0, 0, 28)
    ws.set_column(1, 3, 16)
    ws.set_column(4, 4, 12)

    dine_gmv = channel_data["dine"].get("gmv", pnl.by_code("dine_gmv").amount)
    take_gmv = channel_data["takeout"].get("gmv", pnl.by_code("takeout_gmv").amount)
    dine_net = channel_data["dine"].get("net_sales", dine_gmv)
    take_net = channel_data["takeout"].get("net_sales", take_gmv)
    dine_cogs = channel_data["dine"]["cogs"]
    take_cogs = channel_data["takeout"]["cogs"]
    dine_gp = dine_net - dine_cogs
    take_gp = take_net - take_cogs

    rows_to_write = [
        # (display_name, dine_value, takeout_value, is_subtotal)
        ("GMV (营业额)", dine_gmv, take_gmv, True),
        ("  净销售额 (Net Sales)", dine_net, take_net, False),
        ("  COGS (物料成本)", -dine_cogs, -take_cogs, False),
        ("Gross Profit (销售毛利)", dine_gp, take_gp, True),
        ("Gross Margin % (= GP / Net Sales)",
         dine_gp / dine_net if dine_net else 0,
         take_gp / take_net if take_net else 0,
         False),
    ]
    # 加平台抽佣 (只外卖有)
    commission = abs(pnl.by_code("platform_commission").amount)
    if commission > 0:
        rows_to_write.extend([
            ("  平台抽佣 (估算)", 0, -commission, False),
            ("Contribution Margin (扣抽佣)",
             dine_gp, take_gp - commission, True),
            ("Contribution Margin % (= CM / Net Sales)",
             dine_gp / dine_net if dine_net else 0,
             (take_gp - commission) / take_net if take_net else 0,
             False),
        ])

    for r_idx, (name, dine_v, take_v, is_sub) in enumerate(rows_to_write, start=1):
        # 项目名
        ws.write(r_idx, 0, name,
                 wb.add_format({"bold": is_sub}))

        is_pct = "%" in name
        f = fmt_pct if is_pct else fmt_subtotal if is_sub else fmt_amount

        # 堂食
        f_d = fmt_negative_amount if (dine_v < 0 and not is_pct and not is_sub) else f
        ws.write(r_idx, 1, dine_v, f_d)
        # 外卖
        f_t = fmt_negative_amount if (take_v < 0 and not is_pct and not is_sub) else f
        ws.write(r_idx, 2, take_v, f_t)
        # 合计
        total = dine_v + take_v if not is_pct else None
        if total is not None:
            ws.write(r_idx, 3, total, f)
        else:
            ws.write(r_idx, 3, "")
        # 外卖占比
        if not is_pct and (dine_v + take_v) != 0:
            ratio = take_v / (dine_v + take_v) if not is_pct else None
            ws.write(r_idx, 4, ratio, fmt_pct)

    ws.freeze_panes(1, 1)


# ───────────────────────────────────────────────────────────────
# Sheet 5: 菜单工程矩阵 (Menu Engineering)
#
# 按"销量 × 毛利率"四象限分类:
#                       高销量
#       Plowhorses     │   Stars         (旗舰: 主推)
#       (走量低利)     │
#       提价/换料      │   保护毛利
#       ───────────────┼───────────────
#       Dogs           │   Puzzles       (好货卖不动)
#       (双输, 下架)   │   (营销推一下)
#                       低销量
# ───────────────────────────────────────────────────────────────

def _classify_menu_quadrant(qty: float, margin: float,
                             qty_median: float, margin_median: float) -> str:
    """按销量 / 毛利率两条中线分四象限."""
    hi_qty = qty >= qty_median
    hi_mgn = margin >= margin_median
    if hi_qty and hi_mgn:
        return "Stars ⭐"
    if hi_qty and not hi_mgn:
        return "Plowhorses 🐴"
    if not hi_qty and hi_mgn:
        return "Puzzles 🧩"
    return "Dogs 🐕"


def _compute_menu_engineering(
    sales_rows,
    bom_data,
    combo_structure,
    *,
    uploaded_prices,
    erp_prices,
    price_layers,
    bom_layers,
    strict_price,
):
    """按 (store, item) 算每个 SKU 的 销量 / 营业额 / cost / 毛利率, 然后分四象限.

    Returns: list of dict {store_num, store_name, item_name, qty, revenue,
                            cost, margin, quadrant, action}
    跟 Sheet 5 一一对应.
    """
    from bq_reports.profit_margin_report import (
        _match_bom_layered,
        _resolve_unit_price_with_source,
    )
    from collections import defaultdict
    import statistics

    # 按 (store, item) 聚合 — 跟 _compute_cogs_from_rows 一致
    by_item: dict = defaultdict(lambda: {
        "name": "", "product_type": 0,
        "store_name": "",
        "qty": 0.0, "actual": 0.0,
    })
    for row in sales_rows:
        key = (row.store_num, str(row.item_uuid))
        e = by_item[key]
        e["qty"] += float(row.qty or 0)
        e["actual"] += float(row.actual_amount or 0)
        if not e["name"]:
            e["name"] = row.item_name or ""
            e["store_name"] = row.store_name or ""
            e["product_type"] = int(row.product_type or 0)

    # 算每个 SKU 的 per_unit_cost (复用 _compute_cogs_from_rows 单 SKU 逻辑)
    rows_out = []
    for (store_num, item_uuid), e in by_item.items():
        if e["qty"] <= 0 or e["actual"] <= 0:
            continue
        item_name = e["name"]
        is_combo = e["product_type"] == 1
        store_boms = bom_data.get(store_num, {})

        # fallback_bom 命中
        matched, _layer = _match_bom_layered(item_name, bom_layers)
        if matched:
            bom_list = []
            for code, name, bom_num, _u in matched:
                base_price, _ = _resolve_unit_price_with_source(
                    code, 0, uploaded_prices, erp_prices,
                    price_layers=price_layers, strict=strict_price,
                    material_name=name,
                )
                bom_list.append((bom_num, base_price))
        elif is_combo:
            store_struct = combo_structure.get(store_num, {})
            child_specs = store_struct.get(item_uuid, [])
            agg: dict = {}
            for spec in child_specs:
                if isinstance(spec, (tuple, list)) and len(spec) == 3:
                    child_uuid, child_num, weight = spec
                else:
                    child_uuid, child_num, weight = spec, 1.0, 1.0
                child_mult = float(child_num) * float(weight)
                child_bom = store_boms.get(str(child_uuid), [])
                for mcode, _n, bnum, _u, cr, bqp in child_bom:
                    if not mcode:
                        continue
                    bp, _ = _resolve_unit_price_with_source(
                        mcode, bqp, uploaded_prices, erp_prices,
                        price_layers=price_layers, strict=strict_price,
                    )
                    up = bp * (cr or 1)
                    weighted = bnum * child_mult
                    if mcode in agg:
                        prev_n, prev_p = agg[mcode]
                        agg[mcode] = (prev_n + weighted, up)
                    else:
                        agg[mcode] = (weighted, up)
            bom_list = list(agg.values())
        else:
            item_bom = store_boms.get(item_uuid, [])
            bom_list = []
            seen = set()
            for mcode, _n, bnum, _u, cr, bqp in item_bom:
                if not mcode or mcode in seen:
                    continue
                seen.add(mcode)
                bp, _ = _resolve_unit_price_with_source(
                    mcode, bqp, uploaded_prices, erp_prices,
                    price_layers=price_layers, strict=strict_price,
                )
                bom_list.append((bnum, bp * (cr or 1)))

        per_unit_cost = sum(n * p for n, p in bom_list)
        total_cost = per_unit_cost * e["qty"]
        gross_profit = e["actual"] - total_cost
        margin = gross_profit / e["actual"] if e["actual"] else 0

        rows_out.append({
            "store_num": store_num,
            "store_name": e["store_name"],
            "item_name": item_name,
            "is_combo": is_combo,
            "qty": e["qty"],
            "revenue": e["actual"],
            "cost": total_cost,
            "gross_profit": gross_profit,
            "margin": margin,
        })

    if not rows_out:
        return []

    # 算销量 / 毛利率 中位数 (各店分别算更准, 但简化先用全集团中位数)
    qty_median = statistics.median(r["qty"] for r in rows_out)
    margin_median = statistics.median(r["margin"] for r in rows_out)

    quadrant_actions = {
        "Stars ⭐":       "保护毛利, 主推",
        "Plowhorses 🐴": "走量低利 — 提价 / 换低成本料",
        "Puzzles 🧩":    "好货卖不动 — 营销推广",
        "Dogs 🐕":        "双输 — 考虑下架",
    }

    for r in rows_out:
        r["quadrant"] = _classify_menu_quadrant(
            r["qty"], r["margin"], qty_median, margin_median
        )
        r["action"] = quadrant_actions[r["quadrant"]]

    return rows_out


def _write_menu_engineering_sheet(wb, menu_rows: list, qty_median, margin_median):
    """Sheet 5 菜单工程矩阵 - 每行一个 (店, SKU) + 象限分类 + 建议动作."""
    ws = wb.add_worksheet("菜单工程")

    fmt_header = wb.add_format({
        "bold": True, "bg_color": "#4472C4", "font_color": "white",
        "align": "center", "border": 1,
    })
    fmt_amount = wb.add_format({"num_format": _FMT_AMOUNT})
    fmt_pct = wb.add_format({"num_format": _FMT_PCT})
    fmt_negative_pct = wb.add_format(
        {"num_format": _FMT_PCT, "font_color": "#9C0006"}
    )

    quadrant_colors = {
        "Stars ⭐":       wb.add_format({"bg_color": "#C6EFCE", "align": "center"}),
        "Plowhorses 🐴": wb.add_format({"bg_color": "#FFEB9C", "align": "center"}),
        "Puzzles 🧩":    wb.add_format({"bg_color": "#DDEBF7", "align": "center"}),
        "Dogs 🐕":        wb.add_format({"bg_color": "#FFC7CE", "align": "center"}),
    }

    # 顶部标注: 销量/毛利中位数 + 四象限图例
    ws.merge_range(
        0, 0, 0, 7,
        f"菜单工程矩阵 • 销量中位数 {qty_median:.0f} | 毛利率中位数 {margin_median*100:.1f}%",
        wb.add_format({"bold": True, "font_size": 12}),
    )
    ws.merge_range(
        1, 0, 1, 7,
        "Stars ⭐ 高量高利(主推) | Plowhorses 🐴 高量低利(提价) | "
        "Puzzles 🧩 低量高利(推广) | Dogs 🐕 低量低利(下架)",
        wb.add_format({"italic": True, "font_color": "#666"}),
    )

    headers = ["店编号", "店名", "商品", "类型", "销量", "营业额", "毛利", "毛利率", "象限", "建议"]
    for col, h in enumerate(headers):
        ws.write(3, col, h, fmt_header)
    ws.set_column(0, 0, 8)
    ws.set_column(1, 1, 28)
    ws.set_column(2, 2, 30)
    ws.set_column(3, 3, 6)
    ws.set_column(4, 4, 10)
    ws.set_column(5, 6, 14)
    ws.set_column(7, 7, 10)
    ws.set_column(8, 8, 14)
    ws.set_column(9, 9, 28)

    # 按 (店, 营业额) 降序排
    menu_rows = sorted(menu_rows, key=lambda r: (r["store_num"], -r["revenue"]))
    for r_idx, r in enumerate(menu_rows, start=4):
        ws.write(r_idx, 0, r["store_num"])
        ws.write(r_idx, 1, r["store_name"])
        ws.write(r_idx, 2, r["item_name"])
        ws.write(r_idx, 3, "套餐" if r["is_combo"] else "单品")
        ws.write(r_idx, 4, r["qty"], fmt_amount)
        ws.write(r_idx, 5, r["revenue"], fmt_amount)
        ws.write(r_idx, 6, r["gross_profit"], fmt_amount)
        ws.write(r_idx, 7, r["margin"],
                 fmt_negative_pct if r["margin"] < 0 else fmt_pct)
        ws.write(r_idx, 8, r["quadrant"], quadrant_colors[r["quadrant"]])
        ws.write(r_idx, 9, r["action"])

    ws.freeze_panes(4, 3)
    ws.autofilter(3, 0, 3 + len(menu_rows), len(headers) - 1)


# ───────────────────────────────────────────────────────────────
# Sheet 7: 跨期差异分解 (Variance Decomposition - P5 集成)
# ───────────────────────────────────────────────────────────────

def _write_variance_sheet(wb, artifact: dict, previous_artifact: Optional[dict]):
    """Sheet 7: 本期 vs 上期 Gross Profit 差异 4 维分解 (量 / 价 / 成本 / 结构).

    drill-down 价值: 老板问"为什么这个月毛利掉了 ¥2k", 机器自动给量/价/成本/结构
    四维归因.
    """
    from semantic.analytics import decompose_gross_profit

    ws = wb.add_worksheet("跨期差异分解")
    fmt_header = wb.add_format({
        "bold": True, "bg_color": "#4472C4", "font_color": "white",
        "align": "center", "border": 1,
    })
    fmt_amount = wb.add_format({"num_format": _FMT_AMOUNT})
    fmt_pct = wb.add_format({"num_format": _FMT_PCT})
    fmt_meta = wb.add_format({"italic": True, "font_color": "#666666"})

    if previous_artifact is None:
        ws.merge_range(0, 0, 0, 3,
                       "差异分解 — 待上月数据接入 (传 previous_pnl_artifact 给 main)",
                       fmt_meta)
        return

    cur_pnl = artifact["pnl"]
    prev_pnl = previous_artifact["pnl"]
    cur_totals = artifact["sales_totals"]
    prev_totals = previous_artifact["sales_totals"]

    cur_net = cur_pnl.by_code("net_sales").amount
    prev_net = prev_pnl.by_code("net_sales").amount
    cur_cogs = abs(cur_pnl.by_code("cogs").amount)
    prev_cogs = abs(prev_pnl.by_code("cogs").amount)
    cur_qty = float(cur_totals.get("qty", 0))
    prev_qty = float(prev_totals.get("qty", 0))

    # 平均单价 + 平均单位成本 (跨 SKU 加权代理)
    cur_avg_price = cur_net / cur_qty if cur_qty else 0
    prev_avg_price = prev_net / prev_qty if prev_qty else 0
    cur_avg_cost = cur_cogs / cur_qty if cur_qty else 0
    prev_avg_cost = prev_cogs / prev_qty if prev_qty else 0

    gv = decompose_gross_profit(
        previous_qty=prev_qty, previous_price=prev_avg_price,
        previous_unit_cost=prev_avg_cost,
        current_qty=cur_qty, current_price=cur_avg_price,
        current_unit_cost=cur_avg_cost,
    )

    ws.merge_range(
        0, 0, 0, 3,
        f"Gross Profit 差异分解  {prev_pnl.period} → {cur_pnl.period}",
        wb.add_format({"bold": True, "font_size": 12}),
    )
    ws.merge_range(
        1, 0, 1, 3,
        f"上期 GP {gv.previous_gp:,.0f}  →  本期 GP {gv.current_gp:,.0f}  "
        f"差异 {gv.total_delta:+,.0f}",
        wb.add_format({"italic": True}),
    )

    headers = ["维度", "贡献金额", "占总差异 %", "说明"]
    for col, h in enumerate(headers):
        ws.write(3, col, h, fmt_header)
    ws.set_column(0, 0, 14)
    ws.set_column(1, 1, 16)
    ws.set_column(2, 2, 12)
    ws.set_column(3, 3, 60)

    for r_idx, v in enumerate(gv.variances, start=4):
        ws.write(r_idx, 0, v.name)
        ws.write(r_idx, 1, v.amount, fmt_amount)
        if v.pct_of_total is not None:
            ws.write(r_idx, 2, v.pct_of_total, fmt_pct)
        else:
            ws.write(r_idx, 2, "—")
        ws.write(r_idx, 3, v.note, fmt_meta)

    # Reconcile 校验行
    s = sum(v.amount for v in gv.variances)
    ws.write(4 + len(gv.variances), 0, "合计 (= total_delta)",
             wb.add_format({"bold": True, "top": 2}))
    ws.write(4 + len(gv.variances), 1, s,
             wb.add_format({"bold": True, "top": 2, "num_format": _FMT_AMOUNT}))


# ───────────────────────────────────────────────────────────────
# Sheet 6: 数据来源审计 (Audit Trail)
# ───────────────────────────────────────────────────────────────

def _write_source_audit_sheet(wb, artifact: dict):
    """每个 P&L 行带 source_table / source_cte / formula / confidence.

    drill-down 价值: 任何数字都能查到"哪个 BQ 表 / 哪段 SQL / 哪条公式 来的",
    财务/审计要看的就是这表.
    """
    ws = wb.add_worksheet("数据来源审计")

    fmt_header = wb.add_format({
        "bold": True, "bg_color": "#4472C4", "font_color": "white",
        "align": "center", "border": 1,
    })
    fmt_amount = wb.add_format({"num_format": _FMT_AMOUNT})

    # 数据可信度颜色
    conf_fmts = {
        "actual": wb.add_format({"bg_color": "#C6EFCE", "font_color": "#006100",
                                  "align": "center"}),
        "derived": wb.add_format({"bg_color": "#DDEBF7", "align": "center"}),
        "estimated": wb.add_format({"bg_color": "#FFEB9C", "font_color": "#9C5700",
                                     "align": "center"}),
        "n/a": wb.add_format({"italic": True, "font_color": "#999",
                               "align": "center"}),
    }

    headers = ["P&L 项目", "金额", "Confidence",
               "BQ 源表", "Semantic CTE", "计算公式"]
    for col, h in enumerate(headers):
        ws.write(0, col, h, fmt_header)
    ws.set_column(0, 0, 32)
    ws.set_column(1, 1, 16)
    ws.set_column(2, 2, 12)
    ws.set_column(3, 3, 50)
    ws.set_column(4, 4, 18)
    ws.set_column(5, 5, 60)

    for r_idx, layer in enumerate(artifact["pnl"].layers, start=1):
        ws.write(r_idx, 0, layer.name_zh,
                 wb.add_format({"bold": layer.is_subtotal, "indent": layer.indent}))
        if layer.confidence.value == "n/a":
            ws.write(r_idx, 1, "N/A", conf_fmts["n/a"])
        else:
            ws.write(r_idx, 1, layer.amount, fmt_amount)
        ws.write(r_idx, 2, layer.confidence.value,
                 conf_fmts.get(layer.confidence.value, conf_fmts["n/a"]))
        ws.write(r_idx, 3, layer.source_table)
        ws.write(r_idx, 4, layer.source_cte)
        ws.write(r_idx, 5, layer.formula)

    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, len(artifact["pnl"].layers), len(headers) - 1)


# ───────────────────────────────────────────────────────────────
# CLI
# ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="财务 P&L 损益表导出")
    parser.add_argument("--month", required=True, help="期间 YYYY-MM")
    parser.add_argument("--output", default=None, help="输出 Excel 路径")
    parser.add_argument("--project", default="diyl-407103", help="GCP project")
    parser.add_argument("--config", default=None, help="资源配置 YAML")
    parser.add_argument("--merchants", default="resources/merchants.xlsx",
                        help="商家列表 Excel 路径")
    parser.add_argument(
        "--resolvers", default=None,
        help="resolvers.yaml 路径 (P3 fact_overrides commission_rate)",
    )
    parser.add_argument("--price-list", default=None, help="上传物料价格清单 Excel")
    parser.add_argument("--erp-price-list", default=None, help="ERPNext 价格表名")
    parser.add_argument("--no-erp-price", action="store_true",
                        help="禁用 ERPNext 价格查询")
    parser.add_argument("--allow-erp-fallback", action="store_true",
                        help="允许物料单价 fallback 到 ERPNext (默认 strict)")
    parser.add_argument("--commission-default-rate", type=float, default=0.28,
                        help="无 resolvers 时的默认估算抽佣率")
    parser.add_argument("--compare-with", default=None,
                        help="跟某月对比, 格式 YYYY-MM (Sheet 7 差异分解用)")
    parser.add_argument("--skip-menu", action="store_true",
                        help="跳过 Sheet 5 菜单工程 (大集团时省时间)")
    parser.add_argument("--force", action="store_true",
                        help="强制导出即使校验未通过 (文件将带水印, 不得对外交付)")
    args = parser.parse_args()

    assert_month_not_frozen(args.month)
    setup_proxy()

    # 延迟 import 避免循环
    from bq_reports.profit_margin_report import (
        _load_boms,
        _load_bom_layers,
        _load_combo_structures,
        _load_material_price_layers,
        _load_merchants,
        _load_store_names,
        _load_uploaded_prices,
        _try_load_erp_prices,
        load_config,
    )
    from semantic.resolvers import DictProvider, Resolver

    output_path = args.output or f"exports/pnl_{args.month.replace('-', '')}.xlsx"

    # ── 配置 + Resolvers 加载 ──
    config = load_config(args.config)
    engine = ReportEngine(project_id=args.project)
    start_ts, end_ts = month_to_ts_range(args.month)

    store_names = _load_store_names(config, client=engine.client)
    merchants = _load_merchants(
        config, store_names, override_path=args.merchants, project_id=args.project
    )
    print(f"\n[P&L] 期间 {args.month} | 店数 {len(merchants)}\n")

    uploaded_prices = {}
    if args.price_list:
        uploaded_prices, _ = _load_uploaded_prices(args.price_list)

    erp_prices = {}
    if not args.no_erp_price:
        erp_prices = _try_load_erp_prices(price_list=args.erp_price_list)

    price_layers = _load_material_price_layers(config)
    bom_layers = _load_bom_layers(config)
    strict_price = not bool(args.allow_erp_fallback)

    # commission_rate Resolver (P3) — yaml 没配就用 default-rate
    commission_resolver = None
    if args.resolvers and os.path.exists(args.resolvers):
        resolvers = load_resolvers_from_yaml(
            args.resolvers, allowed_categories=["commission_rate"],
        )
        commission_resolver = resolvers.get("commission_rate")
        if commission_resolver:
            print(f"[Resolvers] commission_rate loaded from {args.resolvers}\n")

    if commission_resolver is None:
        commission_resolver = Resolver([
            DictProvider(
                name=f"default_{int(args.commission_default_rate*100)}pct",
                priority=0,
                data={"default": args.commission_default_rate},
            )
        ], name="commission_rate_inline")
        print(f"[Resolvers] commission_rate fallback: {args.commission_default_rate}\n")

    # ── BQ 拉数 ──
    print("=" * 60)
    print("P&L 销售域拉数")
    print("=" * 60)
    sales_rows = _fetch_pnl_sales_rows(engine, merchants, start_ts, end_ts)
    print(f"\n[P&L] 拉到 {len(sales_rows)} 个 (店 × item × price × channel) 行")

    # ── COGS by channel ──
    print("\n" + "=" * 60)
    print("COGS 计算 (按 channel + 套餐摊薄 + fallback_bom 覆盖)")
    print("=" * 60)
    bom_data = _load_boms(engine, merchants, config)
    combo_structure = _load_combo_structures(
        engine, merchants, start_ts, end_ts, config
    )

    # 同时算: 全集团 COGS + 每店 COGS (per-store P&L 用)
    cogs_per_store = _compute_cogs_per_store(
        sales_rows, bom_data, combo_structure,
        uploaded_prices=uploaded_prices,
        erp_prices=erp_prices,
        price_layers=price_layers,
        bom_layers=bom_layers,
        strict_price=strict_price,
    )
    # 汇总成集团总数
    cogs_data = {
        "dine":    sum(c["dine"] for c in cogs_per_store.values()),
        "takeout": sum(c["takeout"] for c in cogs_per_store.values()),
        "total":   sum(c["total"] for c in cogs_per_store.values()),
    }
    print(f"\n[COGS] 堂食 {cogs_data['dine']:>15,.2f}")
    print(f"[COGS] 外卖 {cogs_data['takeout']:>15,.2f}")
    print(f"[COGS] 合计 {cogs_data['total']:>15,.2f}")

    # ── 编排 + Excel ──
    print("\n" + "=" * 60)
    print("P&L 计算 + Excel 输出")
    print("=" * 60)
    artifact = build_pnl_artifact(
        period=args.month,
        scope="全集团",
        sales_rows=sales_rows,
        cogs_data=cogs_data,
        commission_rate_resolver=commission_resolver,
    )

    # 按店 PnlStatement (Sheet 3)
    per_store = _per_store_artifacts(
        sales_rows, cogs_per_store, commission_resolver, args.month
    )

    # 按渠道 cogs + net_sales (Sheet 4 用)
    # net_sales 按 channel 拆: 直接 SUM sales_rows.actual_amount by channel
    from collections import defaultdict
    net_sales_by_channel: dict = defaultdict(float)
    gmv_by_channel: dict = defaultdict(float)
    for row in sales_rows:
        ch = getattr(row, "channel", None)
        if ch in ("dine", "takeout"):
            net_sales_by_channel[ch] += float(getattr(row, "actual_amount", 0) or 0)
            gmv_by_channel[ch] += float(getattr(row, "sales_price", 0) or 0)
    channel_data = {
        "dine":    {"cogs": cogs_data["dine"],
                    "net_sales": net_sales_by_channel["dine"],
                    "gmv": gmv_by_channel["dine"]},
        "takeout": {"cogs": cogs_data["takeout"],
                    "net_sales": net_sales_by_channel["takeout"],
                    "gmv": gmv_by_channel["takeout"]},
    }

    # 菜单工程 (Sheet 5) - 可跳
    menu_rows = None
    if not args.skip_menu:
        print("\n[Sheet 5] 菜单工程矩阵 — 按 SKU 分四象限")
        menu_rows = _compute_menu_engineering(
            sales_rows, bom_data, combo_structure,
            uploaded_prices=uploaded_prices,
            erp_prices=erp_prices,
            price_layers=price_layers,
            bom_layers=bom_layers,
            strict_price=strict_price,
        )
        print(f"  -> {len(menu_rows)} 个 (店 × SKU) 行")

    # 跨期对比 (Sheet 7) - 可选
    previous_artifact = None
    if args.compare_with:
        print(f"\n[Sheet 7] 跨期差异分解 — 拉 {args.compare_with} 数据")
        prev_start, prev_end = month_to_ts_range(args.compare_with)
        prev_sales = _fetch_pnl_sales_rows(engine, merchants, prev_start, prev_end)
        prev_cogs = _compute_cogs_per_store(
            prev_sales, bom_data, combo_structure,
            uploaded_prices=uploaded_prices, erp_prices=erp_prices,
            price_layers=price_layers, bom_layers=bom_layers,
            strict_price=strict_price,
        )
        prev_cogs_data = {
            "dine": sum(c["dine"] for c in prev_cogs.values()),
            "takeout": sum(c["takeout"] for c in prev_cogs.values()),
            "total": sum(c["total"] for c in prev_cogs.values()),
        }
        previous_artifact = build_pnl_artifact(
            period=args.compare_with, scope="全集团",
            sales_rows=prev_sales,
            cogs_data=prev_cogs_data,
            commission_rate_resolver=commission_resolver,
        )
        print(f"  -> 上期 Net Sales {previous_artifact['pnl'].by_code('net_sales').amount:,.0f}")

    # ── 店粒度销售恒等式 check_rows (技术债⑤已还 PR-B Task 5) ──
    # 从 sales_rows (item × price × channel 粒度) 聚合到 store 粒度,
    # 累加 DEFAULT_IDENTITIES 所需的全部销量/金额桶.
    # net_qty 按定义式推导: qty − free − give − refund − cancelled
    # (SALES_QTY_IDENTITY 是定义式守卫, 防字段缺失/schema 漂移; 参见 identities.py 注释)
    _store_buckets: dict = {}
    for _row in sales_rows:
        _snum = _row.store_num
        if _snum not in _store_buckets:
            _store_buckets[_snum] = {
                "store_num": _snum,
                "qty": 0.0, "sales_price": 0.0, "gross_amount": 0.0,
                "actual_amount": 0.0,
                "refund_qty": 0.0, "refund_amount": 0.0,
                "free_qty": 0.0, "free_amount": 0.0,
                "give_qty": 0.0, "give_amount": 0.0,
                "discount_amount": 0.0,
                "cancelled_qty": 0.0, "cancelled_amount": 0.0,
            }
        _b = _store_buckets[_snum]
        _b["qty"]              += float(getattr(_row, "qty", 0) or 0)
        _b["sales_price"]      += float(getattr(_row, "sales_price", 0) or 0)
        _b["gross_amount"]     += float(getattr(_row, "gross_amount", 0) or 0)
        _b["actual_amount"]    += float(getattr(_row, "actual_amount", 0) or 0)
        _b["refund_qty"]       += float(getattr(_row, "refund_qty", 0) or 0)
        _b["refund_amount"]    += float(getattr(_row, "refund_amount", 0) or 0)
        _b["free_qty"]         += float(getattr(_row, "free_qty", 0) or 0)
        _b["free_amount"]      += float(getattr(_row, "free_amount", 0) or 0)
        _b["give_qty"]         += float(getattr(_row, "give_qty", 0) or 0)
        _b["give_amount"]      += float(getattr(_row, "give_amount", 0) or 0)
        _b["discount_amount"]  += float(getattr(_row, "discount_amount", 0) or 0)
        _b["cancelled_qty"]    += float(getattr(_row, "cancelled_qty", 0) or 0)
        _b["cancelled_amount"] += float(getattr(_row, "cancelled_amount", 0) or 0)
    sales_check_rows = []
    for _b in _store_buckets.values():
        _net_qty = (_b["qty"] - _b["free_qty"] - _b["give_qty"]
                    - _b["refund_qty"] - _b["cancelled_qty"])
        sales_check_rows.append({**_b, "revenue": _b["actual_amount"], "net_qty": _net_qty})

    write_pnl_excel(
        artifact, output_path,
        per_store_artifacts=per_store,
        channel_data=channel_data,
        menu_rows=menu_rows,
        previous_artifact=previous_artifact,
        force=args.force,
        sales_check_rows=sales_check_rows,
    )

    # ── P4 跨系统对账: ttpos anchor ──
    print("\n" + "=" * 60)
    print("跨系统对账 (P4)")
    print("=" * 60)
    from semantic.reconciliation import TtposAnchorCheck, run_checks
    bq_net_sales = artifact["pnl"].by_code("net_sales").amount
    ttpos_amount = _fetch_ttpos_net_sales(engine, merchants, start_ts, end_ts)
    anchor_check = TtposAnchorCheck(
        name=f"TTPOS Anchor {args.month}",
        bq_net_sales=bq_net_sales,
        ttpos_net_sales=ttpos_amount,
    )
    [anchor_result] = run_checks([anchor_check])
    severity_marker = {
        "negligible": "✅",
        "needs_review": "🟡",
        "must_fix": "🔴",
    }.get(anchor_result.severity.name.lower(), "  ")
    print(f"\n{severity_marker} {anchor_result.check_name}")
    print(f"   {anchor_result.summary}")
    if anchor_result.discrepancies:
        for d in anchor_result.discrepancies:
            print(f"   ⚠️  {d.note}")
    print(f"\n[输出] {output_path}")

    # ── 关键数字打印 ──
    pnl = artifact["pnl"]
    print(f"\n=== 关键数字 ({args.month} 全集团) ===")
    for code in ["gmv", "net_sales", "cogs", "gross_profit",
                 "platform_commission", "contribution_margin"]:
        layer = pnl.by_code(code)
        if layer:
            marker = " ★" if layer.is_subtotal else "  "
            print(f"  {marker} {layer.name_zh:<32} {layer.amount:>18,.2f}")

    # KPI 概览
    print(f"\n=== KPI 概览 ===")
    for kpi in artifact["kpis"]:
        if kpi.value is None:
            continue
        if kpi.format == "percent":
            v = f"{kpi.value * 100:.1f}%"
        else:
            v = f"{kpi.value:,.2f}"
        health_marker = {"healthy": "✅", "warning": "🟡", "critical": "🔴",
                         "acceptable": "⚪", "n/a": " "}.get(kpi.health.value, " ")
        print(f"  {health_marker} {kpi.name_zh:<32} {v:>10}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
