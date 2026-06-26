#!/usr/bin/env python3
"""双跑快照 — 整数化前浮点引擎基准 (2026-06)

目的:
    Task 7 将把 sale_event / sale_line / takeout_line 等 CTE 中的交易金额列
    整数化 (satang INT64)。Task 8 合入后重跑本脚本的等价版本并对比 JSON diff，
    以验证整数化前后数字等价 (spec §6 B 双跑验收, SNAPSHOT-FIRST 策略)。

本脚本必须在整数化合入前运行，否则将覆盖浮点基准快照，使 diff 失去意义。

覆盖范围 (仅交易金额 + 数量桶，不包含浮点估算域 COGS/利润/平台费):
    profit_margin  管道: 使用 sale_line / takeout_line 路径 (COMBO + SINGLE 合并)
        字段: qty, net_qty, free_qty, give_qty, refund_qty, cancelled_qty,
              sales_price, gross_amount, original_amount, revenue(actual),
              refund_amount, free_amount, give_amount, discount_amount,
              cancelled_amount
    profit_by_price 管道: 使用 sale_event 路径 (COMBO + SINGLE 合并)
        字段: qty, free_qty, give_qty, refund_qty, cancelled_qty,
              sales_price, gross_amount, original_amount, actual_amount,
              refund_amount, free_amount, give_amount, discount_amount,
              cancelled_amount

输出:
    exports/.dual_run/float_baseline_202606.json
    结构: {"meta": {...}, "rows": {"profit_margin": [...], "profit_by_price": [...]}}

Usage:
    venv/bin/python scripts/adhoc/dump_float_baseline_202606.py 2>&1 | tee /tmp/float_baseline.txt

警告: 必须在整数化合入前运行 — 合入后运行将覆盖浮点基准，使双跑 diff 失效。
"""

import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ── 让项目根目录进 sys.path (不依赖 pip install -e) ──────────────────────────
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
OUTPUT_PATH = Path("exports/.dual_run/float_baseline_202606.json")
PROJECT = PROJECT_ID

# ── 每管道的快照字段列表 ──────────────────────────────────────────────────────

# profit_margin 管道: aggregate_with_bom 输出 key list
PM_FIELDS = [
    "qty", "net_qty", "free_qty", "give_qty",
    "refund_qty", "cancelled_qty",
    "sales_price", "gross_amount", "original_amount", "revenue",
    "refund_amount", "free_amount", "give_amount", "discount_amount",
    "cancelled_amount",
]

# profit_by_price 管道: sale_event METRIC_COLUMNS (已覆盖交易金额+数量桶)
PBP_FIELDS = list(SALE_EVENT_METRICS)  # qty, sales_price, gross_amount, ... (14 fields)


# ── Row proxy factory (同 profit_margin_report.main) ─────────────────────────

def _row_proxy_factory(row, acc, num, name):
    return type("RowProxy", (), {
        "__getattr__": lambda self, attr: getattr(row, attr),
        "account": acc,
        "store_num": num,
        "store_name": name,
    })()


# ── profit_margin 快照收集 ────────────────────────────────────────────────────

def collect_profit_margin(engine, merchants, start_ts, end_ts, config):
    """对 COMBO + SINGLE 各跑一次 engine.query + aggregate_with_bom，
    合并到 per (store_num, item_uuid, item_name) 粒度，只保留 PM_FIELDS。

    不做 BOM/成本展开 — 本脚本只对比交易金额，不涉及浮点估算域。
    """
    combo_structure = _load_combo_structures(engine, merchants, start_ts, end_ts, config)
    bom_data = _load_boms(engine, merchants, config)

    aggregated = {}  # key=(store_num, item_uuid, item_name) → {field: float}

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

        # aggregate_with_bom 返回 {(store_num, store_name, item_uuid, item_name): {...}}
        # 传空 uploaded/erp prices — 我们只要交易金额字段，不算成本
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
                aggregated[key] = {f: 0.0 for f in PM_FIELDS}
            for f in PM_FIELDS:
                aggregated[key][f] += float(data.get(f) or 0)

    # 转成 JSON-serializable list
    rows = []
    for (store_num, item_uuid, item_name), metrics in sorted(aggregated.items()):
        rows.append({
            "store_num": store_num,
            "item_uuid": item_uuid,
            "item_name": item_name,
            **{f: round(metrics[f], 6) for f in PM_FIELDS},
        })
    return rows


# ── profit_by_price 快照收集 ──────────────────────────────────────────────────

FINE_GRAIN_KEYS = ["store_num", "store_name", "item_uuid", "item_name", "price"]


def collect_profit_by_price(engine, merchants, start_ts, end_ts):
    """对 COMBO + SINGLE 各跑一次 engine.query + aggregate_by_grain (fine-grain),
    然后 rollup 到 per (store_num, item_uuid, item_name) 粒度，只保留 PBP_FIELDS。
    """
    aggregated = {}  # key=(store_num, item_uuid, item_name) → {field: float}

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

        # Fine-grain (店, SKU, price) 聚合
        grouped = aggregate_by_grain(raw_rows, FINE_GRAIN_KEYS, PBP_FIELDS)
        print(f"  [profit_by_price/{label}] fine-grain {len(grouped)} 行")

        # Rollup 到 per (store_num, item_uuid, item_name)
        for fine_key, metrics in grouped.items():
            store_num, _store_name, item_uuid, item_name, _price = fine_key
            key = (str(store_num), str(item_uuid), str(item_name))
            if key not in aggregated:
                aggregated[key] = {f: 0.0 for f in PBP_FIELDS}
            for f in PBP_FIELDS:
                aggregated[key][f] += float(metrics.get(f) or 0)

    rows = []
    for (store_num, item_uuid, item_name), metrics in sorted(aggregated.items()):
        rows.append({
            "store_num": store_num,
            "item_uuid": item_uuid,
            "item_name": item_name,
            **{f: round(metrics[f], 6) for f in PBP_FIELDS},
        })
    return rows


# ── Console summary ───────────────────────────────────────────────────────────

def print_summary(pipeline, rows, fields):
    print(f"\n{'='*60}")
    print(f"[{pipeline}] 行数: {len(rows)}")
    if not rows:
        return
    totals = {f: sum(r.get(f, 0) or 0 for r in rows) for f in fields}
    print(f"[{pipeline}] 全局合计:")
    for f, v in totals.items():
        print(f"  {f:25s}: {v:,.2f}")


# ── git HEAD ──────────────────────────────────────────────────────────────────

def _git_head():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(Path(__file__).resolve().parents[2]),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    setup_proxy()
    start_ts, end_ts = month_to_ts_range(MONTH)

    print(f"\n{'='*60}")
    print(f"双跑快照 — 浮点引擎基准  月份={MONTH}")
    print(f"start_ts={start_ts}  end_ts={end_ts}")
    print(f"输出: {OUTPUT_PATH}")
    print(f"{'='*60}\n")

    engine = ReportEngine(project_id=PROJECT)
    config = load_config()
    store_names = _load_store_names(config, client=engine.client)
    merchants = _load_merchants(config, store_names, project_id=PROJECT)
    print(f"门店数: {len(merchants)}")

    # ── profit_margin 管道 ────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("管道 1/2: profit_margin (sale_line / takeout_line)")
    print("="*60)
    pm_rows = collect_profit_margin(engine, merchants, start_ts, end_ts, config)
    print_summary("profit_margin", pm_rows, PM_FIELDS)

    # ── profit_by_price 管道 ──────────────────────────────────────────────────
    print("\n" + "="*60)
    print("管道 2/2: profit_by_price (sale_event)")
    print("="*60)
    pbp_rows = collect_profit_by_price(engine, merchants, start_ts, end_ts)
    print_summary("profit_by_price", pbp_rows, PBP_FIELDS)

    # ── 写 JSON ───────────────────────────────────────────────────────────────
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "meta": {
            "month": MONTH,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "git_head": _git_head(),
            "pipelines": {
                "profit_margin": {
                    "description": "sale_line + takeout_line 路径 (COMBO+SINGLE 合并)",
                    "grain": ["store_num", "item_uuid", "item_name"],
                    "fields": PM_FIELDS,
                },
                "profit_by_price": {
                    "description": "sale_event 路径 (COMBO+SINGLE 合并, fine-grain rollup)",
                    "grain": ["store_num", "item_uuid", "item_name"],
                    "fields": PBP_FIELDS,
                },
            },
        },
        "rows": {
            "profit_margin": pm_rows,
            "profit_by_price": pbp_rows,
        },
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"快照写出: {OUTPUT_PATH}  ({OUTPUT_PATH.stat().st_size:,} bytes)")
    print(f"profit_margin   行数: {len(pm_rows)}")
    print(f"profit_by_price 行数: {len(pbp_rows)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
