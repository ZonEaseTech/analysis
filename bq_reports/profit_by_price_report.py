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
from pathlib import Path

import xlsxwriter

from bq_reports.profit_margin_report import (
    _load_combo_structures,
    _load_boms,
    _load_fallback_boms,
    _load_merchants,
    _load_store_names,
    _load_uploaded_prices,
    _match_fallback_bom,
    _resolve_base_unit_price,
    _try_load_erp_prices,
    load_config,
)
from bq_reports.utils.bq_client import setup_proxy
from semantic.aggregations.by_grain import aggregate_by_grain
from semantic.dimensions.time import month_to_ts_range
from semantic.entities import sale_event
from semantic.validators import check, print_result
from semantic.validators.identities import DEFAULT_IDENTITIES
from utils.report_engine import ReportEngine, load_sheet_config


# ============================================================================
# SQL: sale_event + JOIN to product_package for name; filter by product_type
# ============================================================================

_BY_PRICE_SQL_TPL = f"""
WITH
{sale_event.sale_event_cte()}
SELECT
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
JOIN `{{project}}`.`{{dataset}}`.`ttpos_product_package` pp
  ON pp.uuid = e.item_uuid
WHERE pp.product_type = {{product_type}}
  AND e.qty > 0
"""

COMBO_BY_PRICE_SQL = _BY_PRICE_SQL_TPL.replace("{product_type}", "1")
SINGLE_BY_PRICE_SQL = _BY_PRICE_SQL_TPL.replace("{product_type}", "0")


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


def _build_by_price_rows(grouped, bom_data, combo_structure, mode,
                         fallback_boms=None, uploaded_prices=None, erp_prices=None):
    """Per-SKU + 横向价格档 + BOM 物料展开。

    输入: fine-grain (店, SKU, price) 聚合。
    流程: 先 _rollup_per_sku 收 SKU 级 + price_tiers；再每 SKU 按 BOM 物料展开成 N 行。

    列结构 (37 visible + 1 hidden) 见 resources/reports/profit_by_price.yaml。
    """
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
        # 利润 4 件套 + hidden uuid （suffix）
        suffix = [
            None,                                   # 33 单份总成本    AH  (SUMPRODUCT)
            None,                                   # 34 单品毛利      AI  (formula)
            None,                                   # 35 总毛利        AJ  (formula)
            None,                                   # 36 净利润率      AK  (formula)
            item_uuid_str,                          # 37 hidden item_uuid
        ]

        bom_list = _bom_for_item(
            store_num, item_uuid_str, item_name,
            bom_data, combo_structure, mode,
            fallback_boms, uploaded_prices, erp_prices,
        )

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
                       fallback_boms=None, uploaded_prices=None, erp_prices=None,
                       top_n: int = 10):
    """单份总成本 = 0 但有销量 = 数据异常（最常见原因：BOM 或物料价格未加载）。

    跟会计恒等式（必须严格成立）不同 —— 这是业务合理性检查。同一个 SKU
    出现 0 成本不会数学冲突，但意味着利润率虚假为 100%，客户看到会以为
    商品 100% 利润，是 BUG 不是 FEATURE。

    返回触发 SKU 列表（caller 决定怎么打印）。
    """
    # rollup 到 per-SKU 粒度做 sanity（跟最终 Excel 行粒度一致，避免逐价格档重复警告）
    by_sku = _rollup_per_sku(grouped)
    triggered = []
    for sku_key in sorted(by_sku.keys()):
        store_num, store_name, item_uuid, item_name = sku_key
        item_uuid_str = str(item_uuid)
        data = by_sku[sku_key]
        if data["qty"] <= 0:
            continue
        bom_list = _bom_for_item(
            store_num, item_uuid_str, item_name,
            bom_data, combo_structure, mode,
            fallback_boms, uploaded_prices, erp_prices,
        )
        per_unit_cost = sum(num * unit_price for _, _, num, unit_price, _ in bom_list)
        if per_unit_cost == 0:
            triggered.append((sku_key, data["qty"], data["actual_amount"]))
    return triggered


def _bom_for_item(store_num, item_uuid, item_name, bom_data, combo_structure,
                  mode, fallback_boms, uploaded_prices, erp_prices):
    """Compute BOM list with prices for one SKU.

    Same logic as profit_margin's aggregate_with_bom, lifted out here so cost
    calculation is decoupled from order aggregation. BOM is price-invariant —
    a SKU's recipe doesn't change because it's sold at a discount.
    """
    store_boms = bom_data.get(store_num, {})

    if mode == "combo":
        # 套餐：累加所有子产品 BOM，按 (child_num × weight) 加权。
        # combo_structure v2 元素 = (child_uuid, child_num, weight)；
        # 旧 shape (纯 str) 兼容: num=1, weight=1
        store_struct = combo_structure.get(store_num, {})
        merged = {}
        for spec in store_struct.get(item_uuid, []):
            # JSON cache 把 tuple 序列化为 list，所以两种都得识别
            if isinstance(spec, (tuple, list)) and len(spec) == 3:
                child_uuid, child_num, weight = spec
            else:
                # 旧 shape 兼容: 纯字符串 child_uuid (synthetic 测试用)
                child_uuid, child_num, weight = spec, 1.0, 1.0
            child_mult = float(child_num) * float(weight)
            for material_code, material_name, bom_num, bom_unit, conv_rate, bq_price in store_boms.get(child_uuid, []):
                if not material_code:
                    continue
                base_price = _resolve_base_unit_price(material_code, bq_price, uploaded_prices, erp_prices)
                unit_price = base_price * (conv_rate or 1)
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
        # 单品：直接匹配 + dedup（避免同 material 多 product_bom 行虚增）
        seen = set()
        bom_list = []
        for material_code, material_name, bom_num, bom_unit, conv_rate, bq_price in store_boms.get(item_uuid, []):
            if not material_code or material_code in seen:
                continue
            seen.add(material_code)
            base_price = _resolve_base_unit_price(material_code, bq_price, uploaded_prices, erp_prices)
            unit_price = base_price * (conv_rate or 1)
            bom_list.append((material_code, material_name, bom_num, unit_price, bom_unit))

    # Fallback BOM 补全
    if not bom_list and fallback_boms:
        matched = _match_fallback_bom(item_name, fallback_boms)
        if matched:
            for code, name, bom_num, uom in matched:
                unit_price = _resolve_base_unit_price(code, 0, uploaded_prices, erp_prices)
                bom_list.append((code, name, bom_num, unit_price, uom or "-"))

    # Shape: [(code, name, num, unit_price, uom), ...]
    return [(c, n, num, up, uom)
            for entry in bom_list
            for c, n, num, up, uom in [entry if len(entry) == 5 else (entry[0], entry[1], entry[2], entry[3], entry[4])]]


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

    store_names = _load_store_names(config)
    merchants = _load_merchants(config, store_names,
                                 override_path=args.merchants, project_id=args.project)
    if not merchants:
        print("[错误] 商家列表为空")
        return 1

    print(f"[配置] {len(merchants)} 个门店，月份 {args.month}")

    combo_structure = _load_combo_structures(engine, merchants, start_ts, end_ts, config)
    bom_data = _load_boms(engine, merchants, config)
    fallback_boms = _load_fallback_boms(config)

    modes = ["combo", "single"] if args.mode == "both" else [args.mode]

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    wb = xlsxwriter.Workbook(str(output_path))

    for mode in modes:
        item_label = "套餐" if mode == "combo" else "单品"
        sql_template = COMBO_BY_PRICE_SQL if mode == "combo" else SINGLE_BY_PRICE_SQL
        print(f"\n========== 处理 {item_label} ==========")

        raw_rows, errors = engine.query(
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
        )

        # Fine-grain (店, SKU, price) 聚合 — 用于收集每 SKU 价格档列表
        # 行展开时 rollup 到 per-SKU
        grouped = aggregate_by_grain(raw_rows, FINE_GRAIN_KEYS, METRIC_KEYS)

        # Flatten: per-SKU 行 + 横向价格档 + BOM 物料展开
        flat_rows = _build_by_price_rows(
            grouped, bom_data, combo_structure, mode,
            fallback_boms=fallback_boms,
            uploaded_prices=uploaded_prices, erp_prices=erp_prices,
        )

        sheet_cfg = engine.load_sheet_config(args.column_config, item_label)
        engine.write_sheet(wb, item_label, sheet_cfg, flat_rows)
        print(f"[{item_label}] {len(flat_rows)} 行")

        # Accounting-identity validation (per fine-grain key — finer = 更严)
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
        print(f"\n[{item_label}] 校验恒等式 …")
        result = check(check_rows, DEFAULT_IDENTITIES)
        print_result(
            result,
            row_label=lambda r: f"店 {r['store_num']:>3}  {r['item_name']:<24}  @¥{r['price']}",
        )
        if result.has_must_fix:
            print(f"⚠️  [{item_label}] 有 🔴 离谱违反，发出前请核实数据/口径。\n")

        # 业务合理性 sanity check（独立于会计恒等式）
        zero_cost = _sanity_check_cost(
            grouped, bom_data, combo_structure, mode,
            fallback_boms=fallback_boms,
            uploaded_prices=uploaded_prices, erp_prices=erp_prices,
        )
        if zero_cost:
            print(f"\n[{item_label}] 🟠 单份总成本=0 异常: {len(zero_cost)} SKU 行")
            print(f"   常见原因: BOM 未配 / ERPNext 价格未加载 / 物料无价")
            print(f"   后果: 利润率显示为 100% (实际无意义)")
            for (k, q, actual) in sorted(zero_cost, key=lambda x: -x[2])[:10]:
                # sku_key 现在是 (店编, 店名, item_uuid, SKU名)，已不含 price
                print(f"   店 {k[0]:>3}  {k[3]:<28}  qty={q:>5.0f}  实收 ¥{actual:>10.0f}")
            if len(zero_cost) > 10:
                print(f"   ⏬ 还有 {len(zero_cost)-10} 条略")

    wb.close()
    print(f"\n输出文件: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
