#!/usr/bin/env python3
"""按价利润报表 — 客户交付物。

每行 = (店, SKU, 价格档)；同一 SKU 多档价格出现多行，按店号顺序。

跟 profit_margin（中间表）的区别：
  profit_margin   行 = (店, SKU, BOM 物料)，32 列，作为对账锚保留
  profit_by_price 行 = (店, SKU, 价格档)，18 列，给客户/需求方

共享底座：
  semantic/entities/sale_event.py        最细粒度事实表
  semantic/aggregations/by_grain.py      通用 GROUP BY
  semantic/validators/                   会计恒等式校验

下次需求"按 channel 展开"/"按天展开"复用上面三个组件，**改 grain_keys 一行**。

Usage:
    venv/bin/python -m bq_reports.profit_by_price_report --month 2026-04
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

import xlsxwriter

from bq_reports.profit_margin_report import (
    _load_bom_layers,
    _load_combo_structures,
    _load_boms,
    _load_fallback_boms,  # 兼容: 兜底导出, 单文件场景仍可用
    _load_material_price_layers,
    _build_material_price_resolver,
    _load_merchants,
    _load_store_names,
    _load_uploaded_prices,
    _match_bom_layered,
    _match_fallback_bom,
    _resolve_base_unit_price,
    _resolve_unit_price_with_source,
    _try_load_erp_prices,
    load_config,
)
from bq_reports.utils.bq_client import setup_proxy
from semantic.aggregations.by_grain import aggregate_by_grain
from semantic.dimensions.time import month_to_ts_range
from semantic.entities import sale_event
from semantic.validators import check, print_result
from semantic.validators.identities import FULL_IDENTITIES
from utils.report_engine import ReportEngine, load_sheet_config


# ============================================================================
# SQL: sale_event + JOIN to product_package for name; filter by product_type
# ============================================================================

def _build_sql_templates(dine_excludes=None, takeout_excludes=None,
                         exclude_test_business=False):
    """构造 (COMBO_SQL, SINGLE_SQL) — 把排除清单注入 sale_event CTE.

    清单为空 → 跟模块加载时的零行为版本完全一致.
    exclude_test_business=True → 对齐 ttpos 后台 ExcludeTestBusinessByBillSQL 口径.
    """
    tpl = f"""
WITH
{sale_event.sale_event_cte(dine_excludes, takeout_excludes, exclude_test_business=exclude_test_business)}
{_BY_PRICE_SELECT_BODY}"""
    return (tpl.replace("{product_type}", "1"),
            tpl.replace("{product_type}", "0"))


# SELECT 主体 (不含 WITH / CTE 部分) — 给 _build_sql_templates 复用
_BY_PRICE_SELECT_BODY = """SELECT
  e.item_uuid,
  e.price,
  e.channel,
  e.qty,
  e.sales_price,
  e.actual_amount,
  e.original_amount,
  e.refund_qty,
  e.refund_amount,
  e.free_qty,
  e.give_qty,
  e.free_amount,
  e.give_amount,
  e.discount_amount,
  e.cancelled_qty,
  e.cancelled_amount,
  -- 商品名：剥不可见字符
  REGEXP_REPLACE(COALESCE(
    JSON_EXTRACT_SCALAR(pp.name, '$.zh'),
    JSON_EXTRACT_SCALAR(pp.name, '$.en'),
    '未知'
  ), r'^\\s+|\\s+$', '') AS item_name
FROM sale_event e
JOIN `{project}`.`{dataset}`.`ttpos_product_package` pp
  ON pp.uuid = e.item_uuid
WHERE pp.product_type = {product_type}
  AND e.qty > 0
"""

# 模块加载时构造默认 (空排除清单) 模板, 兼容老调用方
COMBO_BY_PRICE_SQL, SINGLE_BY_PRICE_SQL = _build_sql_templates()


def _load_order_excludes(config) -> tuple[set, set]:
    """加载 config.order_excludes 指向的 CSV → (dine_set, takeout_set).

    报表层排除清单 — 不动生产数据, 改 CSV 即可生效.
    格式: store_num,channel,key_type,exclude_key,reason,added_at
      channel ∈ {dine, takeout}
      key_type ∈ {sale_order_uuid, takeout_uuid} (注释用, 由 channel 决定语义)
      exclude_key: 雪花 uuid (NUMERIC). 全店唯一, 跨 dataset NOT IN 安全.
    """
    import csv as _csv
    spec = (config or {}).get("order_excludes") or {}
    path = spec.get("path")
    if not path or not os.path.exists(path):
        return set(), set()
    dine, takeout = set(), set()
    with open(path, encoding="utf-8-sig") as f:
        for r in _csv.DictReader(f):
            ch = (r.get("channel") or "").strip()
            key = (r.get("exclude_key") or "").strip()
            if not key:
                continue
            try:
                k = int(key)
            except ValueError:
                continue
            if ch == "dine":
                dine.add(k)
            elif ch == "takeout":
                takeout.add(k)
    return dine, takeout


# ============================================================================
# Aggregation: events → (store, item, price) grain → rows with cost/profit
# ============================================================================

# 两层 grain:
#   FINE_GRAIN: (店, SKU, price) — sale_event 天然粒度，用于收集每 SKU 各价格档的净销量
#   GRAIN_KEYS: (店, SKU)       — 最终行粒度（横向铺开价格档）
# channel 不参与 grain（堂食 + 外卖同价合并）
FINE_GRAIN_KEYS = ["store_num", "store_name", "item_uuid", "item_name", "price"]
GRAIN_KEYS = ["store_num", "store_name", "item_uuid", "item_name"]
METRIC_KEYS = sale_event.METRIC_COLUMNS    # qty / sales_price / actual_amount / …
TOP_N_PRICES = 5                            # 横向展开前 N 档；其余落到"其它销量"


def _rollup_per_sku(fine_grouped: dict) -> dict:
    """从 fine-grain (店, SKU, price) → 粗 grain (店, SKU) + 收集价格档列表。

    Returns: dict[(店编, 店名, item_uuid, item_name)] = {
        ...summed metric_keys...,
        'price_tiers': [(price, net_qty_at_this_price), ...] sorted desc by net_qty
    }
    """
    by_sku: dict = {}
    for fine_key, metrics in fine_grouped.items():
        store_num, store_name, item_uuid, item_name, price = fine_key
        sku_key = (store_num, store_name, item_uuid, item_name)
        if sku_key not in by_sku:
            by_sku[sku_key] = {m: 0.0 for m in METRIC_KEYS}
            by_sku[sku_key]["price_tiers"] = []
        for m in METRIC_KEYS:
            by_sku[sku_key][m] += metrics.get(m, 0) or 0
        # 单档净销量
        net_at_price = (metrics["qty"] - metrics["free_qty"] - metrics["give_qty"]
                        - metrics["refund_qty"] - metrics["cancelled_qty"])
        by_sku[sku_key]["price_tiers"].append((price, net_at_price))

    for data in by_sku.values():
        data["price_tiers"].sort(key=lambda t: -t[1])
    return by_sku


def _load_platform_fees(config: dict, year_month: str) -> dict:
    """加载外卖平台抽佣事实表 → {store_num: effective_rate}.

    每店当月真实抽佣率 = Σ平台佣金 / Σ平台应收 (合并多平台对账单).
    报表层: SKU 平台抽佣 = SKU takeout revenue × 该店真实费率.
    粒度: (store_num, year_month) — 订单号对不上, 用月级聚合.

    用"费率"而不是"金额按比例"模式的原因:
      套餐 sheet 跟单品 sheet 共享同店总佣金, 直接金额分摊会算两次.
      用费率每 sheet 独立, 加总 ≈ Σ(SKU 外卖 revenue) × rate ≈ 对账单总佣金.

    Returns {} 当 config 没配 / 文件不存在 / 该月无数据 (caller fallback 到费率估算).
    """
    import csv
    sources = config.get("platform_fees") or []
    if not sources:
        return {}
    by_store: dict[str, dict] = {}
    for src in sources:
        path = src.get("path")
        if not path:
            continue
        p = Path(path)
        if not p.exists():
            print(f"[Platform Fees] ⚠️ 文件不存在: {path}, 跳过")
            continue
        with p.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("year_month") != year_month:
                    continue
                store = str(row.get("store_num", "")).strip()
                if not store:
                    continue
                gross = float(row.get("gross_revenue") or 0)
                commission = float(row.get("commission_total") or 0)
                slot = by_store.setdefault(store, {"gross": 0.0, "commission": 0.0})
                slot["gross"] += gross
                slot["commission"] += commission
    # 转成 {store: rate}
    return {
        store: (d["commission"] / d["gross"]) if d["gross"] > 0 else 0.0
        for store, d in by_store.items()
    }


def _build_by_price_rows(grouped, bom_data, combo_structure, mode,
                         bom_layers=None, uploaded_prices=None, erp_prices=None,
                         fallback_boms=None, price_layers=None, strict_price=False,
                         strict_bom=False, revenue_by_channel=None, channel_fees=None,
                         platform_fees_by_store=None, store_takeout_total=None):
    """Per-SKU + 横向价格档 + BOM 物料展开。

    输入: fine-grain (店, SKU, price) 聚合。
    流程: 先 _rollup_per_sku 收 SKU 级 + price_tiers；再每 SKU 按 BOM 物料展开成 N 行。

    列结构 (37 visible + 1 hidden) 见 resources/reports/profit_by_price.yaml。
    """
    if bom_layers is None and fallback_boms:
        bom_layers = [("fallback_bom", 50, fallback_boms)]
    bom_layers = bom_layers or []

    # 构造一次共享 price resolver, 避免 per-SKU/per-BOM-row 重建
    _price_resolver = _build_material_price_resolver(
        uploaded_prices, erp_prices, price_layers, strict=strict_price)

    # 渠道费率 (堂食支付费 = PLACEHOLDER, 外卖平台抽佣 = fallback 当对账单缺失时用)
    channel_fees = channel_fees or {}
    dine_pay_rate = float(channel_fees.get("dine_payment_fee_rate", 0))
    takeout_plat_rate_fallback = float(channel_fees.get("takeout_platform_fee_rate", 0))
    dine_svc_rate = float(channel_fees.get("dine_service_fee_rate", 0))
    revenue_by_channel = revenue_by_channel or {}
    platform_fees_by_store = platform_fees_by_store or {}
    store_takeout_total = store_takeout_total or {}

    by_sku = _rollup_per_sku(grouped)
    rows = []

    for sku_key in sorted(by_sku.keys()):
        store_num, store_name, item_uuid, item_name = sku_key
        # bom_data / combo_structure 的 key 是 str（_load_boms 显式 str cast）
        item_uuid_str = str(item_uuid)
        data = by_sku[sku_key]

        qty = data["qty"]
        net_qty = (qty - data["free_qty"] - data["give_qty"]
                   - data["refund_qty"] - data["cancelled_qty"])

        # Top-N 价格档 + 其它销量
        price_tiers = data["price_tiers"]
        top_n = price_tiers[:TOP_N_PRICES]
        others_net = sum(q for _, q in price_tiers[TOP_N_PRICES:])
        while len(top_n) < TOP_N_PRICES:
            top_n.append((None, None))

        # SKU 级 prefix（merge: true，跨 BOM 行复制）
        prefix = [
            store_num,                              # 0  门店编号        A
            store_name,                             # 1  门店名称        B
            item_name,                              # 2  商品名称        C
            round(qty, 2),                          # 3  销量            D
            round(net_qty, 2),                      # 4  净销量          E
            round(data["sales_price"], 2),          # 5  营业额          F
            round(data["original_amount"], 2),      # 6  标准金额        G
            round(data["actual_amount"], 2),        # 7  实收金额        H  ★
            None, None, None,                       # 8-10 折损/客单实收/实收占比 (formula)
            # 价格档 5 对 + 其它销量
            (round(top_n[0][0], 2) if top_n[0][0] is not None else None),  # 11 售价1
            (round(top_n[0][1], 2) if top_n[0][1] is not None else None),  # 12 净销量1
            (round(top_n[1][0], 2) if top_n[1][0] is not None else None),
            (round(top_n[1][1], 2) if top_n[1][1] is not None else None),
            (round(top_n[2][0], 2) if top_n[2][0] is not None else None),
            (round(top_n[2][1], 2) if top_n[2][1] is not None else None),
            (round(top_n[3][0], 2) if top_n[3][0] is not None else None),
            (round(top_n[3][1], 2) if top_n[3][1] is not None else None),
            (round(top_n[4][0], 2) if top_n[4][0] is not None else None),
            (round(top_n[4][1], 2) if top_n[4][1] is not None else None),
            round(others_net, 2),                   # 21 其它销量        V
            round(data["free_qty"], 2),             # 22 赠品数量        W
            round(data["give_qty"], 2),             # 23 赠送数量        X
            round(data["refund_qty"], 2),           # 24 退款数量        Y
            round(data["refund_amount"], 2),        # 25 退款金额        Z
            round(data["cancelled_qty"], 2),        # 26 取消数量       AA
            round(data["cancelled_amount"], 2),     # 27 取消金额       AB
        ]
        bom_list, bom_source, price_source = _bom_for_item(
            store_num, item_uuid_str, item_name,
            bom_data, combo_structure, mode,
            bom_layers, uploaded_prices, erp_prices,
            price_layers=price_layers, strict_price=strict_price,
            strict_bom=strict_bom, price_resolver=_price_resolver,
        )

        # 渠道分实收 — 用于算 3 项费用
        rev_ch = revenue_by_channel.get((store_num, item_uuid_str), {})
        revenue_dine = float(rev_ch.get("dine", 0))
        revenue_takeout = float(rev_ch.get("takeout", 0))
        payment_fee = revenue_dine * dine_pay_rate
        service_fee_income = revenue_dine * dine_svc_rate
        # 平台抽佣: 优先用对账单事实费率 (该店真实综合费率)
        # fallback: 该店该月没对账单 → 用 config 费率估算
        store_fact_rate = platform_fees_by_store.get(store_num)
        if store_fact_rate is not None and store_fact_rate > 0:
            platform_fee = revenue_takeout * store_fact_rate
        else:
            platform_fee = revenue_takeout * takeout_plat_rate_fallback

        # 利润 4 件套 + hidden uuid + 双审计列 + 净利润 5 列（suffix）
        suffix = [
            None,                                   # 33 单份总成本    AH  (SUMPRODUCT)
            None,                                   # 34 单品毛利      AI  (formula)
            None,                                   # 35 总毛利        AJ  (formula)
            None,                                   # 36 毛利率        AK  (formula)
            item_uuid_str,                          # 37 hidden item_uuid AL
            bom_source,                             # 38 BOM 数量来源     AM
            price_source,                           # 39 物料价来源       AN
            round(payment_fee, 2),                  # 40 支付手续费       AO  (NEW)
            round(platform_fee, 2),                 # 41 平台抽佣         AP  (NEW)
            round(service_fee_income, 2),           # 42 服务费收入       AQ  (NEW)
            None,                                   # 43 净利润           AR  (formula)
            None,                                   # 44 净利率           AS  (formula)
        ]

        if not bom_list:
            rows.append(prefix + ["-", "-", None, None, "-"] + suffix)
            continue

        for code, name, bom_num, unit_price, uom in bom_list:
            rows.append(prefix + [
                name,                               # 28 BOM物品名称  AC
                code,                               # 29 BOM物品编码  AD
                round(bom_num, 4),                  # 30 消耗数量     AE
                round(unit_price, 4),               # 31 物料单价     AF
                uom or "-",                         # 32 单位         AG
            ] + suffix)

    return rows


def _sanity_check_cost(grouped, bom_data, combo_structure, mode,
                       bom_layers=None, uploaded_prices=None, erp_prices=None,
                       fallback_boms=None, price_layers=None, strict_price=False,
                       top_n: int = 10):
    """单份总成本 = 0 但有销量 = 数据异常（最常见原因：BOM 或物料价格未加载）。

    跟会计恒等式（必须严格成立）不同 —— 这是业务合理性检查。同一个 SKU
    出现 0 成本不会数学冲突，但意味着利润率虚假为 100%，客户看到会以为
    商品 100% 利润，是 BUG 不是 FEATURE。

    返回触发 SKU 列表（caller 决定怎么打印）。
    """
    if bom_layers is None and fallback_boms:
        bom_layers = [("fallback_bom", 50, fallback_boms)]
    bom_layers = bom_layers or []
    # 构造一次共享 price resolver, 避免 per-SKU 重建
    _price_resolver = _build_material_price_resolver(
        uploaded_prices, erp_prices, price_layers, strict=strict_price)
    # rollup 到 per-SKU 粒度做 sanity（跟最终 Excel 行粒度一致，避免逐价格档重复警告）
    by_sku = _rollup_per_sku(grouped)
    triggered = []
    for sku_key in sorted(by_sku.keys()):
        store_num, store_name, item_uuid, item_name = sku_key
        item_uuid_str = str(item_uuid)
        data = by_sku[sku_key]
        if data["qty"] <= 0:
            continue
        bom_list, _bs, _ps = _bom_for_item(
            store_num, item_uuid_str, item_name,
            bom_data, combo_structure, mode,
            bom_layers, uploaded_prices, erp_prices,
            price_layers=price_layers, strict_price=strict_price,
            price_resolver=_price_resolver,
        )
        per_unit_cost = sum(num * unit_price for _, _, num, unit_price, _ in bom_list)
        if per_unit_cost == 0:
            triggered.append((sku_key, data["qty"], data["actual_amount"]))
    return triggered


def _bom_for_item(store_num, item_uuid, item_name, bom_data, combo_structure,
                  mode, bom_layers, uploaded_prices, erp_prices,
                  price_layers=None, strict_price=False, strict_bom=False,
                  price_resolver=None):
    """Compute BOM list with prices for one SKU.

    BOM 选取顺序 (跟 profit_margin 一致):
      1. bom_layers 按 priority 高→低逐层匹配, 命中即覆盖 BQ 原生
      2. BQ 原生 (bom_data / combo_structure)  — strict_bom=True 时跳过
      3. 都没 → 空

    strict_bom=True 时禁用第 2 步, 跟 profit_margin_report 对称.

    price_resolver: 调用方 (per-report) 构造一次传进来, 避免 per-SKU/per-BOM-row
    重建整个 Resolver. 不传则内部自建 (兼容旧调用).

    Returns: (bom_list, qty_source_label, price_source_label)
        bom_list: [(code, name, num, unit_price, uom), ...]
        qty_source_label: "bq_native" / bom_layer_name / "无"
        price_source_label: " + " 拼接的 price_layer/uploaded/ERPNext/bq_native/"无 (strict)"
    """
    store_boms = bom_data.get(store_num, {})
    price_sources_seen = set()
    if price_resolver is None:
        price_resolver = _build_material_price_resolver(
            uploaded_prices, erp_prices, price_layers, strict=strict_price)

    def _resolve(code, bq_p, mat_name=None):
        p, src = _resolve_unit_price_with_source(
            code, bq_p, uploaded_prices, erp_prices,
            price_layers=price_layers, strict=strict_price,
            material_name=mat_name, resolver=price_resolver)
        price_sources_seen.add(src)
        return p

    if strict_bom:
        # 跳过 BQ 原生 BOM 计算, bom_list 留空, 等下要么 bom_layers 命中, 要么"无"
        bom_list = []
    elif mode == "combo":
        # 套餐：累加所有子产品 BOM，按 (child_num × weight) 加权。
        store_struct = combo_structure.get(store_num, {})
        merged = {}
        for spec in store_struct.get(item_uuid, []):
            if isinstance(spec, (tuple, list)) and len(spec) == 3:
                child_uuid, child_num, weight = spec
            else:
                child_uuid, child_num, weight = spec, 1.0, 1.0
            child_mult = float(child_num) * float(weight)
            for material_code, material_name, bom_num, bom_unit, conv_rate, bq_price in store_boms.get(child_uuid, []):
                if not material_code:
                    continue
                unit_price = _resolve(material_code, bq_price, material_name) * (conv_rate or 1)
                weighted_bom_num = bom_num * child_mult
                if material_code in merged:
                    prev_name, prev_num, prev_up, prev_unit = merged[material_code]
                    merged[material_code] = (prev_name or material_name,
                                              prev_num + weighted_bom_num, unit_price,
                                              prev_unit or bom_unit)
                else:
                    merged[material_code] = (material_name, weighted_bom_num, unit_price, bom_unit)
        bom_list = [(c, *rest) for c, rest in merged.items()]
    else:
        seen = set()
        bom_list = []
        for material_code, material_name, bom_num, bom_unit, conv_rate, bq_price in store_boms.get(item_uuid, []):
            if not material_code or material_code in seen:
                continue
            seen.add(material_code)
            unit_price = _resolve(material_code, bq_price, material_name) * (conv_rate or 1)
            bom_list.append((material_code, material_name, bom_num, unit_price, bom_unit))

    bom_source = "bq_native" if bom_list else None

    # 外挂 BOM override (priority 栈, 高 priority 覆盖 BQ 原生)
    matched, layer_name = _match_bom_layered(item_name, bom_layers or [])
    if matched:
        bom_list = []
        price_sources_seen = set()   # 覆盖时重置 (BQ 路径的 source 不算)
        for code, name, bom_num, uom in matched:
            unit_price = _resolve(code, 0, name)
            bom_list.append((code, name, bom_num, unit_price, uom or "-"))
        bom_source = layer_name
    if bom_source is None:
        bom_source = "无"

    # Shape: [(code, name, num, unit_price, uom), ...]
    normalized = [(c, n, num, up, uom)
                  for entry in bom_list
                  for c, n, num, up, uom in [entry if len(entry) == 5 else (entry[0], entry[1], entry[2], entry[3], entry[4])]]
    price_source = " + ".join(sorted(price_sources_seen)) if price_sources_seen else "无"
    return normalized, bom_source, price_source


# ============================================================================
# Main
# ============================================================================

def _next_version_path(base_dir: Path, prefix: str, suffix: str = ".xlsx") -> Path:
    """扫描 base_dir 里匹配 `{prefix}_v{N}{suffix}` 的文件，返回下一版本路径。

    无既有版本 → v1；否则取 max(N)+1。**每次都升版**，不做内容去重。
    """
    pattern = re.compile(rf"^{re.escape(prefix)}_v(\d+){re.escape(suffix)}$")
    versions = [int(m.group(1)) for f in base_dir.iterdir()
                if (m := pattern.match(f.name))]
    next_v = (max(versions) + 1) if versions else 1
    return base_dir / f"{prefix}_v{next_v}{suffix}"


def main() -> int:
    parser = argparse.ArgumentParser(description="按价利润报表导出（客户交付物）")
    parser.add_argument("--mode", default="both", choices=["combo", "single", "both"])
    parser.add_argument("--month", required=True, help="月份，YYYY-MM（BKK 时区）")
    parser.add_argument("--merchants", default="resources/merchants.xlsx")
    parser.add_argument("--output", default=None)
    parser.add_argument("--project", default="diyl-407103")
    parser.add_argument("--use-erp-price", action="store_true", default=True)
    parser.add_argument("--no-erp-price", action="store_true")
    parser.add_argument("--erp-price-list", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--column-config", default="resources/reports/profit_by_price.yaml")
    parser.add_argument("--price-list", default=None,
                        help="上传的物料价格清单 Excel 路径（最高优先级）")
    parser.add_argument("--no-cache", action="store_true")
    # 默认 strict: 单价只走 material_price_sources, 缺失留空 + 标黄
    parser.add_argument("--allow-erp-fallback", action="store_true",
                        help="允许 fallback ERPNext (默认禁用)。默认: 缺失即留空, 报表里标黄。")
    # BOM 默认 strict: 只走 bom_sources, 缺失 → BOM来源="无". 跟 profit_margin 对称.
    parser.add_argument("--external", default=None,
                        help="外部销售源, 格式 'provider:key=val', e.g. 'huku:path=/path/to/file.xlsx'")
    parser.add_argument("--allow-bq-native-bom", action="store_true",
                        help="允许 fallback BQ 内置 BOM (默认禁用)。默认: BOM 只走 bom_sources, "
                             "缺失即标 BOM来源='无', 成本列空。")
    parser.add_argument("--force", action="store_true",
                        help="强制导出即使校验未通过 (文件将带水印, 不得对外交付)")
    args = parser.parse_args()

    setup_proxy()
    start_ts, end_ts = month_to_ts_range(args.month)
    # 自动版本号: 默认输出 exports/profit_by_price_{月}_v{N}.xlsx，每次升版。
    # 用户给 --output 时尊重之，不加 _vN（适合 CI/自动化场景）。
    if args.output:
        output_path = Path(args.output)
    else:
        base_dir = Path("exports")
        base_dir.mkdir(parents=True, exist_ok=True)
        output_path = _next_version_path(
            base_dir, f"profit_by_price_{args.month.replace('-', '')}")

    engine = ReportEngine(project_id=args.project)
    config = load_config(args.config)

    uploaded_prices = {}
    if args.price_list:
        uploaded_prices, _ = _load_uploaded_prices(args.price_list)

    erp_prices = {}
    if args.use_erp_price and not args.no_erp_price:
        erp_ttl = 0 if args.no_cache else config.get("cache", {}).get("erp_prices_ttl", 3600)
        erp_prices = _try_load_erp_prices(price_list=args.erp_price_list, cache_ttl=erp_ttl)

    store_names = _load_store_names(config, client=engine.client)
    merchants = _load_merchants(config, store_names,
                                 override_path=args.merchants, project_id=args.project)
    if not merchants:
        print("[错误] 商家列表为空")
        return 1

    print(f"[配置] {len(merchants)} 个门店，月份 {args.month}")

    # 渠道费率 (堂食支付费 PLACEHOLDER, 外卖平台抽佣是 fallback)
    channel_fees = config.get("channel_fees", {}) or {}
    if channel_fees:
        print(f"[Channel Fees] 堂食支付费率={channel_fees.get('dine_payment_fee_rate', 0)*100:.2f}% (PLACEHOLDER), "
              f"外卖平台抽佣 fallback={channel_fees.get('takeout_platform_fee_rate', 0)*100:.2f}%, "
              f"堂食服务费={channel_fees.get('dine_service_fee_rate', 0)*100:.2f}%")

    # 外卖平台抽佣 事实表 (按 (店, 月) 落账, 报表层按 SKU revenue 比例分摊)
    platform_fees_by_store = _load_platform_fees(config, args.month)
    if platform_fees_by_store:
        rates = list(platform_fees_by_store.values())
        avg_rate = sum(rates) / len(rates) * 100
        min_rate = min(rates) * 100
        max_rate = max(rates) * 100
        print(f"[Platform Fees] 加载 {len(platform_fees_by_store)} 店真实抽佣率: "
              f"avg={avg_rate:.2f}% [{min_rate:.2f}% ~ {max_rate:.2f}%]")
    else:
        print(f"[Platform Fees] ⚠️ 未加载到 {args.month} 平台抽佣事实, 全部走 fallback 费率")

    combo_structure = _load_combo_structures(engine, merchants, start_ts, end_ts, config)
    bom_data = _load_boms(engine, merchants, config)
    bom_layers = _load_bom_layers(config)
    price_layers = _load_material_price_layers(config)
    strict_price = not bool(args.allow_erp_fallback)   # 默认 strict
    if strict_price:
        print(f"[Strict Price] 默认严格模式: 物料单价只走外挂成本表, 缺失 → 0")
        print(f"               想恢复 ERP fallback 用 --allow-erp-fallback\n")
    else:
        print(f"[ERP Fallback] 允许 fallback ERPNext\n")

    strict_bom = not bool(args.allow_bq_native_bom)   # 默认 strict
    if strict_bom:
        print(f"[Strict BOM] 默认严格模式: BOM 只走 bom_sources, 缺失 → 来源='无'")
        print(f"             想恢复 BQ 内置 BOM 用 --allow-bq-native-bom\n")
    else:
        print(f"[BQ Native BOM Fallback] 允许 fallback BQ 内置 product_bom\n")

    # 报表层订单排除 (外挂事实表, 不动生产)
    dine_excl, takeout_excl = _load_order_excludes(config)
    if dine_excl or takeout_excl:
        print(f"[Order Excludes] 报表层排除: 堂食 {len(dine_excl)} 单 / 外卖 {len(takeout_excl)} 单 "
              f"(源: {config['order_excludes']['path']})")
        combo_sql_t, single_sql_t = _build_sql_templates(dine_excl, takeout_excl)
    else:
        combo_sql_t, single_sql_t = COMBO_BY_PRICE_SQL, SINGLE_BY_PRICE_SQL

    # 测试营业时段过滤 (对齐 ttpos 后台口径 ExcludeTestBusinessByBillSQL)
    # 只对存在 ttpos_business_status_period 表的店启用 → 按店切换 SQL 模板
    from semantic.dimensions.test_business import get_stores_with_test_business
    merchant_uuids = [m[1] for m in merchants]
    tb_stores = get_stores_with_test_business(engine.client, merchant_uuids)
    if tb_stores:
        print(f"[Test Business] 排除测试营业时段, 影响 {len(tb_stores)} 店: "
              f"{sorted(tb_stores)}")
        combo_sql_tb, single_sql_tb = _build_sql_templates(
            dine_excl, takeout_excl, exclude_test_business=True)
    else:
        combo_sql_tb = single_sql_tb = None

    modes = ["combo", "single"] if args.mode == "both" else [args.mode]

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    wb = xlsxwriter.Workbook(str(output_path))

    # ── 分段计时 (profile) ── 数据驱动: 看瓶颈在哪段再决定要不要并行
    _timings: dict = {}

    def _timed(label, fn):
        t0 = time.perf_counter()
        out = fn()
        _timings[label] = _timings.get(label, 0.0) + time.perf_counter() - t0
        return out

    # ── 外部销售源 (可选) — 不传 --external 时跑法跟纯 BQ 一致 ──────────
    external_rows = []
    if args.external:
        from external_sales import load_external
        print(f"\n========== 外部销售源 {args.external} ==========")
        external_rows = _timed("外部源加载", lambda: load_external(
            args.external,
            bq_client=engine.client, merchants=merchants,
            month=args.month, config=config,
        ))
        ext_rev_total = sum(r.revenue for r in external_rows)
        ext_matched = sum(r.revenue for r in external_rows if not r.is_unmatched)
        print(f"  [外部源] {len(external_rows)} SKU 行, "
              f"实收 {ext_rev_total:,.0f} 泰铢, "
              f"高置信 {ext_matched:,.0f} ({ext_matched/ext_rev_total*100:.1f}%)")

    watermarked = False  # 多 mode 循环只打一次水印
    for mode in modes:
        item_label = "套餐" if mode == "combo" else "单品"
        sql_template = combo_sql_t if mode == "combo" else single_sql_t
        sql_template_tb = combo_sql_tb if mode == "combo" else single_sql_tb
        print(f"\n========== 处理 {item_label} ==========")

        # 按 store 切换: 启用测试营业过滤的店用 _tb 版, 其他用普通版
        sql_factory = (
            (lambda u: sql_template_tb if u in tb_stores else sql_template)
            if tb_stores else None
        )
        raw_rows, errors = _timed("BQ 拉数", lambda: engine.query(
            sql_template=sql_template,
            merchants=merchants,
            start_ts=start_ts,
            end_ts=end_ts,
            workers=10,
            row_proxy_factory=lambda row, acc, num, name: type("RowProxy", (), {
                "__getattr__": lambda self, attr: getattr(row, attr),
                "account": acc, "store_num": num, "store_name": name,
            })(),
            label=item_label,
            sql_template_factory=sql_factory,
        ))

        # Fine-grain (店, SKU, price) 聚合 — 用于收集每 SKU 价格档列表
        # 行展开时 rollup 到 per-SKU
        grouped = _timed("聚合", lambda: aggregate_by_grain(
            raw_rows, FINE_GRAIN_KEYS, METRIC_KEYS))

        # 渠道分实收 — 为净利润算 3 项费用 (key=(store_num, item_uuid_str))
        # 同时算每店 takeout revenue 总额 — 平台抽佣事实按 SKU 比例分摊用
        def _build_revenue_by_channel():
            d = {}
            store_tk_total: dict[str, float] = {}
            for r in raw_rows:
                key = (r.store_num, str(r.item_uuid))
                slot = d.setdefault(key, {"dine": 0.0, "takeout": 0.0})
                amt = float(r.actual_amount or 0)
                slot[r.channel] = slot.get(r.channel, 0.0) + amt
                if r.channel == "takeout":
                    store_tk_total[r.store_num] = store_tk_total.get(r.store_num, 0.0) + amt
            return d, store_tk_total
        revenue_by_channel, store_takeout_total = _timed(
            "渠道拆实收", _build_revenue_by_channel)

        # Flatten: per-SKU 行 + 横向价格档 + BOM 物料展开
        flat_rows = _timed("行构建+BOM展开", lambda: _build_by_price_rows(
            grouped, bom_data, combo_structure, mode,
            bom_layers=bom_layers,
            uploaded_prices=uploaded_prices, erp_prices=erp_prices,
            price_layers=price_layers, strict_price=strict_price,
            strict_bom=strict_bom,
            revenue_by_channel=revenue_by_channel,
            channel_fees=channel_fees,
            platform_fees_by_store=platform_fees_by_store,
            store_takeout_total=store_takeout_total,
        ))

        # 外部源 SKU 行: append 到该 mode 的 flat_rows 末尾
        if external_rows:
            from external_sales import to_profit_by_price_rows
            ext_flat = to_profit_by_price_rows(external_rows, mode)
            bq_n = len(flat_rows)
            flat_rows = flat_rows + ext_flat
            print(f"[{item_label}] BQ {bq_n} 行 + 外部 {len(ext_flat)} 行 = {len(flat_rows)} 行")

        sheet_cfg = engine.load_sheet_config(args.column_config, item_label)
        _timed("写 Excel", lambda: engine.write_sheet(
            wb, item_label, sheet_cfg, flat_rows))
        if not external_rows:
            print(f"[{item_label}] {len(flat_rows)} 行")

        # 零容差闸门: 校验恒等式 + sanity (FULL_IDENTITIES)
        # fine-grain (店, SKU, price) 粒度检查 — 更严
        # 闸门在 wb.close() 前执行 — 有 MUST_FIX 且无 --force 则 exit(2), 不落盘
        from semantic.validators.gate import (
            add_watermark_sheet_xlsxwriter, validate_and_gate)

        check_rows = [
            {
                "store_num": k[0], "item_name": k[3], "price": k[4],
                "net_qty": v["qty"] - v["free_qty"] - v["give_qty"]
                           - v["refund_qty"] - v["cancelled_qty"],
                "revenue": v["actual_amount"],
                **v,
            }
            for k, v in grouped.items()
        ]
        outcome = _timed("校验恒等式", lambda: validate_and_gate(
            check_rows, FULL_IDENTITIES,
            force=args.force, report_name=f"profit_by_price/{item_label}",
            row_label=lambda r: f"店 {r['store_num']:>3}  {r['item_name']:<24}  @¥{r['price']}",
        ))
        if outcome.needs_watermark and not watermarked:
            add_watermark_sheet_xlsxwriter(wb, outcome.watermark_lines())
            watermarked = True

        # 业务合理性 sanity check（独立于会计恒等式）
        zero_cost = _timed("sanity check", lambda: _sanity_check_cost(
            grouped, bom_data, combo_structure, mode,
            bom_layers=bom_layers,
            uploaded_prices=uploaded_prices, erp_prices=erp_prices,
            price_layers=price_layers, strict_price=strict_price,
        ))
        if zero_cost:
            print(f"\n[{item_label}] 🟠 单份总成本=0 异常: {len(zero_cost)} SKU 行")
            print(f"   常见原因: BOM 未配 / ERPNext 价格未加载 / 物料无价")
            print(f"   后果: 利润率显示为 100% (实际无意义)")
            for (k, q, actual) in sorted(zero_cost, key=lambda x: -x[2])[:10]:
                # sku_key 现在是 (店编, 店名, item_uuid, SKU名)，已不含 price
                print(f"   店 {k[0]:>3}  {k[3]:<28}  qty={q:>5.0f}  实收 ¥{actual:>10.0f}")
            if len(zero_cost) > 10:
                print(f"   ⏬ 还有 {len(zero_cost)-10} 条略")

    _t0 = time.perf_counter()
    wb.close()
    _timings["wb.close (落盘)"] = time.perf_counter() - _t0

    print(f"\n输出文件: {output_path}")

    # ── 分段耗时汇总 (profile) ──
    print("\n=== 分段耗时 profile (两 mode 累加) ===")
    _total = sum(_timings.values())
    for label, secs in sorted(_timings.items(), key=lambda x: -x[1]):
        pct = 100 * secs / _total if _total else 0
        print(f"  {label:18} {secs:8.2f}s  ({pct:4.1f}%)")
    print(f"  {'合计':18} {_total:8.2f}s")
    # NOTE: 物料单价为空标黄的后处理跑 openpyxl 太慢 (15 MB / 100K 行 → 15 分钟+, 3GB+ 内存),
    # 暂时关掉. 客户筛"价来源 = 无 (strict)" 就能定位缺价 SKU.
    # 后续要做 → 改用 xlsxwriter format 在写入时同步标黄, 不要事后开 openpyxl.
    return 0


if __name__ == "__main__":
    sys.exit(main())
