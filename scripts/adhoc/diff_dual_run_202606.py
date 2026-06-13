#!/usr/bin/env python3
"""双跑 diff 验收 — 浮点基准 vs 整数引擎 (2026-06)

目的:
    Task 6 在整数化(7a)合入前，把浮点引擎的 2026-06 聚合快照存入
    exports/.dual_run/float_baseline_202606.json (THB float).
    本脚本在整数化(7a/7b/7c)合入后运行相同聚合，得到萨当 INT64 输出，
    然后与快照对比，证明整数化前后数字等价（差异 ≤ 1 satang = 0.01 THB）。

比较逻辑:
    基准侧: float_baseline THB  (取自 JSON，原始单位)
    引擎侧: engine satang / 100.0 (转换为 THB 后再比较)
    容忍度: |diff| ≤ 0.01 THB (= 1 satang)
    数量桶: 精确整数相等 (qty/net_qty/free_qty/give_qty/refund_qty/cancelled_qty)

行键: (store_num, item_uuid, item_name) — 与 dump 脚本相同粒度

Usage:
    venv/bin/python scripts/adhoc/diff_dual_run_202606.py 2>&1 | tee /tmp/dual_diff.txt
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bq_reports.utils.bq_client import setup_proxy, PROJECT_ID
from bq_reports.profit_margin_report import (
    COMBO_ORDERS_SQL,
    SINGLE_ORDERS_SQL,
    aggregate_with_bom,
    load_config,
    _load_merchants,
    _load_store_names,
    _load_combo_structures,
    _load_boms,
)
from bq_reports.profit_by_price_report import (
    COMBO_BY_PRICE_SQL,
    SINGLE_BY_PRICE_SQL,
)
from semantic.aggregations.by_grain import aggregate_by_grain
from semantic.dimensions.time import month_to_ts_range
from semantic.entities.sale_event import METRIC_COLUMNS as SALE_EVENT_METRICS
from utils.report_engine import ReportEngine

MONTH = "2026-06"
BASELINE_PATH = Path("exports/.dual_run/float_baseline_202606.json")
PROJECT = PROJECT_ID

TOLERANCE_THB = 0.01  # 1 satang

# ── 字段分类 ─────────────────────────────────────────────────────────────────

# profit_margin 管道字段（与 dump_float_baseline_202606.py 的 PM_FIELDS 相同）
PM_MONEY_FIELDS = [
    "sales_price", "gross_amount", "original_amount", "revenue",
    "refund_amount", "free_amount", "give_amount", "discount_amount",
    "cancelled_amount",
]
PM_QTY_FIELDS = ["qty", "net_qty", "free_qty", "give_qty", "refund_qty", "cancelled_qty"]
PM_FIELDS = PM_QTY_FIELDS + PM_MONEY_FIELDS

# profit_by_price 管道字段（与 dump_float_baseline_202606.py 的 PBP_FIELDS 相同）
PBP_MONEY_FIELDS = [
    "sales_price", "gross_amount", "original_amount", "actual_amount",
    "refund_amount", "free_amount", "give_amount", "discount_amount",
    "cancelled_amount",
]
PBP_QTY_FIELDS = ["qty", "refund_qty", "free_qty", "give_qty", "cancelled_qty"]
PBP_FIELDS = list(SALE_EVENT_METRICS)  # 保持与 dump 脚本完全一致

FINE_GRAIN_KEYS = ["store_num", "store_name", "item_uuid", "item_name", "price"]


# ── Row proxy factory (同 profit_margin_report.main) ──────────────────────────

def _row_proxy_factory(row, acc, num, name):
    return type("RowProxy", (), {
        "__getattr__": lambda self, attr: getattr(row, attr),
        "account": acc,
        "store_num": num,
        "store_name": name,
    })()


# ── 整数引擎: profit_margin 管道 ───────────────────────────────────────────────

def run_profit_margin(engine, merchants, start_ts, end_ts, config):
    """跑整数引擎的 profit_margin 管道，返回 {(store_num, item_uuid, item_name): data_dict}。
    money 字段是萨当 INT64，数量字段是 float。
    """
    combo_structure = _load_combo_structures(engine, merchants, start_ts, end_ts, config)
    bom_data = _load_boms(engine, merchants, config)

    aggregated = {}  # key=(store_num, item_uuid, item_name) → {field: value}

    for mode, sql in [("combo", COMBO_ORDERS_SQL), ("single", SINGLE_ORDERS_SQL)]:
        label = "套餐" if mode == "combo" else "单品"
        print(f"\n[profit_margin] 拉数 ({label}) ...")
        raw_rows, errors = engine.query(
            sql_template=sql,
            merchants=merchants,
            start_ts=start_ts,
            end_ts=end_ts,
            workers=10,
            row_proxy_factory=_row_proxy_factory,
            label=f"PM-{label}",
        )
        if errors:
            print(f"  [警告] {len(errors)} 个门店查询失败: {list(errors.keys())[:5]}")

        agg = aggregate_with_bom(
            raw_rows, bom_data, combo_structure,
            uploaded_prices={}, erp_prices={},
            mode=mode,
            price_layers=None, strict_price=True,
        )
        print(f"  [profit_margin/{label}] {len(agg)} (store, sku) 行")

        for (store_num, _store_name, item_uuid, item_name), data in agg.items():
            key = (str(store_num), str(item_uuid), str(item_name))
            if key not in aggregated:
                aggregated[key] = {f: 0 for f in PM_FIELDS}
                # qty fields are float
                for f in PM_QTY_FIELDS:
                    aggregated[key][f] = 0.0
            for f in PM_FIELDS:
                v = data.get(f) or 0
                if f in PM_QTY_FIELDS:
                    aggregated[key][f] += float(v)
                else:
                    # money: satang INT64 (integer addition exact)
                    aggregated[key][f] += int(v)

    return aggregated


# ── 整数引擎: profit_by_price 管道 ────────────────────────────────────────────

def run_profit_by_price(engine, merchants, start_ts, end_ts):
    """跑整数引擎的 profit_by_price 管道，返回 {(store_num, item_uuid, item_name): data_dict}。
    money 字段是萨当 INT64，数量字段是 float。
    """
    aggregated = {}  # key=(store_num, item_uuid, item_name) → {field: value}

    for mode, sql in [("combo", COMBO_BY_PRICE_SQL), ("single", SINGLE_BY_PRICE_SQL)]:
        label = "套餐" if mode == "combo" else "单品"
        print(f"\n[profit_by_price] 拉数 ({label}) ...")
        raw_rows, errors = engine.query(
            sql_template=sql,
            merchants=merchants,
            start_ts=start_ts,
            end_ts=end_ts,
            workers=10,
            row_proxy_factory=_row_proxy_factory,
            label=f"PBP-{label}",
        )
        if errors:
            print(f"  [警告] {len(errors)} 个门店查询失败: {list(errors.keys())[:5]}")

        grouped = aggregate_by_grain(raw_rows, FINE_GRAIN_KEYS, PBP_FIELDS)
        print(f"  [profit_by_price/{label}] fine-grain {len(grouped)} 行")

        for fine_key, metrics in grouped.items():
            store_num, _store_name, item_uuid, item_name, _price = fine_key
            key = (str(store_num), str(item_uuid), str(item_name))
            if key not in aggregated:
                aggregated[key] = {}
                for f in PBP_FIELDS:
                    aggregated[key][f] = 0.0 if f in PBP_QTY_FIELDS else 0
            for f in PBP_FIELDS:
                v = metrics.get(f) or 0
                if f in PBP_QTY_FIELDS:
                    aggregated[key][f] += float(v)
                else:
                    aggregated[key][f] += int(v)

    return aggregated


# ── Diff 逻辑 ─────────────────────────────────────────────────────────────────

def diff_pipeline(pipeline_name, baseline_rows, engine_agg, money_fields, qty_fields):
    """比较单管道的基准行和引擎聚合。

    Args:
        baseline_rows: JSON 基准行列表（THB float）
        engine_agg: {(store_num, item_uuid, item_name): data_dict}，money=satang，qty=float

    Returns:
        dict with summary stats and over-tolerance rows.
    """
    print(f"\n{'='*60}")
    print(f"DIFF: {pipeline_name}")
    print(f"{'='*60}")

    # 基准行索引
    baseline_index = {}
    for row in baseline_rows:
        key = (str(row["store_num"]), str(row["item_uuid"]), str(row["item_name"]))
        baseline_index[key] = row

    baseline_keys = set(baseline_index.keys())
    engine_keys = set(engine_agg.keys())

    baseline_only = baseline_keys - engine_keys
    engine_only = engine_keys - baseline_keys
    matched_keys = baseline_keys & engine_keys

    keyset_match = (len(baseline_only) == 0 and len(engine_only) == 0)

    print(f"基准行数:       {len(baseline_keys)}")
    print(f"引擎行数:       {len(engine_keys)}")
    print(f"匹配行数:       {len(matched_keys)}")
    print(f"键集完全吻合:   {'YES' if keyset_match else 'NO'}")
    if baseline_only:
        print(f"  仅在基准中:   {len(baseline_only)} 行")
        for k in sorted(baseline_only)[:5]:
            print(f"    {k}")
    if engine_only:
        print(f"  仅在引擎中:   {len(engine_only)} 行")
        for k in sorted(engine_only)[:5]:
            print(f"    {k}")

    # Per-metric diff
    over_tol = []   # [(key, metric, baseline_thb, engine_thb, diff)]
    max_diff_by_field = {f: 0.0 for f in money_fields + qty_fields}

    for key in sorted(matched_keys):
        brow = baseline_index[key]
        erow = engine_agg[key]

        # Money fields: baseline THB (float) vs engine satang / 100.0
        for f in money_fields:
            b_thb = float(brow.get(f) or 0)
            e_satang = int(erow.get(f) or 0)
            e_thb = e_satang / 100.0
            diff = abs(b_thb - e_thb)
            max_diff_by_field[f] = max(max_diff_by_field[f], diff)
            if diff > TOLERANCE_THB:
                over_tol.append((key, f, b_thb, e_thb, b_thb - e_thb))

        # Qty fields: both should be same float; baseline THB values for qty
        # should equal engine float values (qty not converted by /100)
        for f in qty_fields:
            b_val = float(brow.get(f) or 0)
            e_val = float(erow.get(f) or 0)
            diff = abs(b_val - e_val)
            max_diff_by_field[f] = max(max_diff_by_field[f], diff)
            if diff > 0.001:  # small float tolerance for qty
                over_tol.append((key, f, b_val, e_val, b_val - e_val))

    # Per-field max diff summary
    print(f"\n字段级最大绝对差 (money=THB, qty=件):")
    all_fields = money_fields + qty_fields
    for f in all_fields:
        mark = " ⚠️ OVER" if max_diff_by_field[f] > TOLERANCE_THB else ""
        print(f"  {f:25s}: max_diff={max_diff_by_field[f]:.6f}{mark}")

    # Over-tolerance report
    global_max_diff = max((abs(r[4]) for r in over_tol), default=0.0)
    print(f"\n超限行数 (|diff| > {TOLERANCE_THB} THB): {len(over_tol)}")
    if over_tol:
        print(f"{'key':<50} {'metric':<25} {'baseline':>12} {'engine':>12} {'diff':>12}")
        for (key, metric, bv, ev, d) in over_tol[:20]:
            key_str = f"{key[0]}/{key[2][:20]}"
            print(f"  {key_str:<48} {metric:<25} {bv:12.4f} {ev:12.4f} {d:12.4f}")
        if len(over_tol) > 20:
            print(f"  ... (另 {len(over_tol) - 20} 行略)")

    return {
        "pipeline": pipeline_name,
        "baseline_rows": len(baseline_keys),
        "engine_rows": len(engine_keys),
        "matched_rows": len(matched_keys),
        "baseline_only": len(baseline_only),
        "engine_only": len(engine_only),
        "keyset_match": keyset_match,
        "over_tolerance_count": len(over_tol),
        "max_diff_thb": global_max_diff,
        "max_diff_by_field": max_diff_by_field,
        "over_tolerance_rows": over_tol[:20],
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    setup_proxy()

    if not BASELINE_PATH.exists():
        print(f"[ERROR] 基准文件不存在: {BASELINE_PATH}")
        print("BLOCKED — Task 6 基准 JSON 必须在整数化前生成，不可事后重建。")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"双跑 diff 验收 — 浮点基准 vs 整数引擎  月份={MONTH}")
    print(f"基准文件: {BASELINE_PATH}")
    print(f"容忍度: |diff| ≤ {TOLERANCE_THB} THB (= 1 satang)")
    print(f"比较逻辑: 基准 THB vs 引擎萨当/100.0")
    print(f"{'='*60}\n")

    # 加载基准
    with open(BASELINE_PATH, encoding="utf-8") as f:
        baseline = json.load(f)
    print(f"基准元数据:")
    print(f"  generated_at: {baseline['meta']['generated_at']}")
    print(f"  git_head:     {baseline['meta']['git_head']}")
    baseline_pm_rows = baseline["rows"]["profit_margin"]
    baseline_pbp_rows = baseline["rows"]["profit_by_price"]
    print(f"  profit_margin 基准行:   {len(baseline_pm_rows)}")
    print(f"  profit_by_price 基准行: {len(baseline_pbp_rows)}")

    start_ts, end_ts = month_to_ts_range(MONTH)
    engine = ReportEngine(project_id=PROJECT)
    config = load_config()
    store_names = _load_store_names(config, client=engine.client)
    merchants = _load_merchants(config, store_names, project_id=PROJECT)
    print(f"\n门店数: {len(merchants)}")

    # ── 管道 1: profit_margin ────────────────────────────────────────────────
    print("\n" + "="*60)
    print("管道 1/2: profit_margin (sale_line / takeout_line)")
    print("="*60)
    pm_engine_agg = run_profit_margin(engine, merchants, start_ts, end_ts, config)
    pm_result = diff_pipeline(
        "profit_margin",
        baseline_pm_rows,
        pm_engine_agg,
        money_fields=PM_MONEY_FIELDS,
        qty_fields=PM_QTY_FIELDS,
    )

    # ── 管道 2: profit_by_price ──────────────────────────────────────────────
    print("\n" + "="*60)
    print("管道 2/2: profit_by_price (sale_event)")
    print("="*60)
    pbp_engine_agg = run_profit_by_price(engine, merchants, start_ts, end_ts)
    pbp_result = diff_pipeline(
        "profit_by_price",
        baseline_pbp_rows,
        pbp_engine_agg,
        money_fields=PBP_MONEY_FIELDS,
        qty_fields=PBP_QTY_FIELDS,
    )

    # ── 总结 ─────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("双跑 diff 总结")
    print(f"{'='*60}")

    total_over_tol = pm_result["over_tolerance_count"] + pbp_result["over_tolerance_count"]
    all_keyset_ok = pm_result["keyset_match"] and pbp_result["keyset_match"]

    for r in [pm_result, pbp_result]:
        keyset_str = "YES" if r["keyset_match"] else f"NO (仅基准={r['baseline_only']}, 仅引擎={r['engine_only']})"
        print(f"\n[{r['pipeline']}]")
        print(f"  对比行数:   {r['matched_rows']}")
        print(f"  键集吻合:   {keyset_str}")
        print(f"  最大差异:   {r['max_diff_thb']:.6f} THB")
        print(f"  超限行数:   {r['over_tolerance_count']}")

    print(f"\n结论: ", end="")
    if total_over_tol == 0 and all_keyset_ok:
        print("零超限 ✓ — 整数化与浮点引擎数字等价，精度在 1 satang 以内。")
        conclusion = "零超限"
    else:
        issues = []
        if not all_keyset_ok:
            issues.append("键集不一致")
        if total_over_tol > 0:
            issues.append(f"{total_over_tol} 行超限")
        print(f"⚠️  发现问题: {', '.join(issues)} — 需排查原因。")
        conclusion = f"{total_over_tol} 超限"

    return pm_result, pbp_result


if __name__ == "__main__":
    main()
