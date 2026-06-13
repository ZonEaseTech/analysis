#!/usr/bin/env python3
"""跨账本观察跑 — 2026-05 基线 (只观察不阻断)

目的:
  实测统计账 (ttpos_statistics_product) 与凭证账 (sale_bill→sale_order→
  sale_order_product) 在 2026-05 冻结月的数量/金额吻合度，作为
  CROSS_LEDGER_IDENTITIES 升入导出闸门的 PR-A 验收决策依据
  (spec §11; docs/audit/2026-06-cross-ledger-baseline.md).

三个校准问题:
  Q1. 统计账 vs 凭证账: qty/gross 匹配率 & delta 分布.
      时间语义差异 (sp.complete_time vs sb.finish_time) 的影响量.
  Q2. 支付勾稽: 每店 SUM(sale_bill.payment_amount) vs 统计账
      SUM(actual_amount) — PAYMENT_TIEOUT_IDENTITY 校准.
  Q3. 外卖订单勾稽 (TAKEOUT_TIEOUT): 30+ 店缺 ttpos_takeout_order 表,
      先用 INFORMATION_SCHEMA UNION ALL 探测, 缺表门店列 N/A 清单,
      绝不静默跳过.

注意: 2026-05 为冻结月, 本脚本只读不写, 符合 spec §6 "只读 audit 不受冻结限制".

Usage:
    venv/bin/python scripts/adhoc/audit_cross_ledger_202605.py 2>&1 | tee /tmp/cross_ledger_obs.txt
"""

import sys
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID
from bq_reports.profit_margin_report import load_config, _load_merchants, _load_store_names
from semantic.dimensions.time import month_to_ts_range
from semantic.entities.sale_event import sale_event_cte
from semantic.entities.order_line import order_line_cte
from semantic.entities.takeout_tieout import takeout_tieout_cte
from semantic.validators.core import check, print_result
from semantic.validators.identities import (
    CROSS_LEDGER_IDENTITIES,
    TAKEOUT_TIEOUT_IDENTITIES,
    PAYMENT_TIEOUT_IDENTITY,
)

MONTH = "2026-05"


# ─── SQL 模板 ────────────────────────────────────────────────────────────────

# 统计账: item 粒度聚合 + 实收 (for payment tieout)
STAT_SQL = (
    "WITH "
    + sale_event_cte()
    + """
SELECT
  item_uuid,
  ANY_VALUE(CAST(item_uuid AS STRING)) AS item_uuid_str,
  SUM(qty) AS stat_qty,
  SUM(gross_amount) AS stat_gross,
  SUM(actual_amount) AS stat_actual
FROM sale_event
GROUP BY item_uuid
"""
)

# 凭证账: item 粒度
VOUCHER_SQL = (
    "WITH "
    + order_line_cte()
    + """
SELECT
  item_uuid,
  voucher_qty,
  voucher_gross
FROM order_line
"""
)

# 外卖勾稽: 订单粒度
TIEOUT_SQL = (
    "WITH "
    + takeout_tieout_cte()
    + """
SELECT
  order_uuid,
  order_state,
  platform_total,
  merchant_charge_fee,
  merchant_discount,
  item_sum
FROM takeout_tieout
"""
)

# 支付: 账单层实收
PAYMENT_SQL = """
SELECT
  SUM(payment_amount) AS payment_amount_sum
FROM `{project}`.`{dataset}`.`ttpos_sale_bill`
WHERE status = 1
  AND delete_time = 0
  AND finish_time >= {start_ts}
  AND finish_time < {end_ts}
"""

# INFORMATION_SCHEMA probe for ttpos_takeout_order
def _build_takeout_probe_sql(merchants, project_id):
    parts = [
        f"SELECT '{uuid_str}' AS uuid_str, COUNT(*) AS has_table "
        f"FROM `{project_id}.shop{uuid_str}.INFORMATION_SCHEMA.TABLES` "
        f"WHERE table_name = 'ttpos_takeout_order'"
        for _, uuid_str, _, _ in merchants
    ]
    return " UNION ALL ".join(parts)


# ─── Per-store query helpers ─────────────────────────────────────────────────

def _fmt_sql(tpl, project, uuid_str, start_ts, end_ts):
    dataset = f"shop{uuid_str}"
    return tpl.format(
        project=project,
        dataset=dataset,
        start_ts=start_ts,
        end_ts=end_ts,
    )


def _query_store(client, sql, project, uuid_str, start_ts, end_ts):
    full_sql = _fmt_sql(sql, project, uuid_str, start_ts, end_ts)
    return list(client.query(full_sql).result())


def query_all_concurrent(client, sql_tpl, merchants, start_ts, end_ts, label, workers=10):
    """Run sql_tpl across all merchants concurrently.

    Returns: dict[uuid_str → list[Row]], dict[uuid_str → error_str]
    """
    results = {}
    errors = {}

    def _one(m):
        _, uuid_str, store_num, store_name = m
        try:
            rows = _query_store(client, sql_tpl, client.project, uuid_str, start_ts, end_ts)
            return uuid_str, rows, None
        except Exception as e:
            return uuid_str, [], str(e)

    with ThreadPoolExecutor(max_workers=min(workers, len(merchants))) as ex:
        futs = [ex.submit(_one, m) for m in merchants]
        done = 0
        for f in as_completed(futs):
            uuid_str, rows, err = f.result()
            done += 1
            if err:
                errors[uuid_str] = err
                print(f"  [{label}] {done}/{len(merchants)} {uuid_str[:8]}... ERROR: {err[:80]}")
            else:
                results[uuid_str] = rows
                if done % 10 == 0 or done == len(merchants):
                    print(f"  [{label}] {done}/{len(merchants)} 完成")

    return results, errors


# ─── Cross-ledger row builder ─────────────────────────────────────────────────

def build_cross_ledger_rows(stat_rows, voucher_rows, store_num, store_name):
    """Join stat + voucher by item_uuid, produce rows for CROSS_LEDGER_IDENTITIES."""
    stat_by_item = {str(r.item_uuid): r for r in stat_rows}
    voucher_by_item = {str(r.item_uuid): r for r in voucher_rows}
    all_items = set(stat_by_item) | set(voucher_by_item)

    rows = []
    for item_uuid in all_items:
        s = stat_by_item.get(item_uuid)
        v = voucher_by_item.get(item_uuid)
        rows.append({
            "store_num": store_num,
            "store_name": store_name,
            "item_uuid": item_uuid,
            "stat_qty": float(s.stat_qty) if s else 0.0,
            "stat_gross": float(s.stat_gross) if s else 0.0,
            "voucher_qty": float(v.voucher_qty) if v else 0.0,
            "voucher_gross": float(v.voucher_gross) if v else 0.0,
            "voucher_present": 1.0 if v else 0.0,
        })
    return rows


# ─── Delta distribution helpers ──────────────────────────────────────────────

def percentile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    idx = int(len(sorted_vals) * p / 100)
    idx = min(idx, len(sorted_vals) - 1)
    return sorted_vals[idx]


def delta_stats(all_cl_rows, field_stat, field_voucher):
    deltas = sorted(abs(float(r[field_stat]) - float(r[field_voucher])) for r in all_cl_rows)
    if not deltas:
        return {"p50": 0, "p95": 0, "max": 0, "nonzero": 0, "total": 0}
    nonzero = sum(1 for d in deltas if d > 0)
    return {
        "p50": percentile(deltas, 50),
        "p95": percentile(deltas, 95),
        "max": deltas[-1],
        "nonzero": nonzero,
        "total": len(deltas),
    }


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    setup_proxy()
    client = get_bq_client(PROJECT_ID)

    start_ts, end_ts = month_to_ts_range(MONTH)
    print(f"\n{'='*60}")
    print(f"跨账本观察跑  月份={MONTH}  start_ts={start_ts}  end_ts={end_ts}")
    print(f"{'='*60}\n")

    # 加载配置 + 商家列表 (same pattern as profit_margin_report.main)
    config = load_config()
    store_names = _load_store_names(config, client=client)
    merchants = _load_merchants(config, store_names, project_id=PROJECT_ID)
    print(f"门店数: {len(merchants)}\n")

    # 建立 uuid → (store_num, store_name) 映射
    uuid_to_meta = {uuid_str: (snum, sname) for _, uuid_str, snum, sname in merchants}

    # ── Step 1: 探测哪些店有 ttpos_takeout_order ─────────────────────────────
    print("=== Step 1: 探测外卖表 ===")
    probe_sql = _build_takeout_probe_sql(merchants, client.project)
    has_takeout = set()
    no_takeout = []
    for r in client.query(probe_sql).result():
        if r.has_table > 0:
            has_takeout.add(r.uuid_str)
    for _, uuid_str, snum, sname in merchants:
        if uuid_str not in has_takeout:
            no_takeout.append((snum, sname, uuid_str))
    print(f"  有 ttpos_takeout_order 的门店: {len(has_takeout)}")
    print(f"  缺表门店 (N/A): {len(no_takeout)}")
    merchants_with_takeout = [(a, u, s, n) for a, u, s, n in merchants if u in has_takeout]

    # ── Step 2: 查询统计账 (STAT) ─────────────────────────────────────────────
    print("\n=== Step 2: 查询统计账 (sale_event) ===")
    stat_results, stat_errors = query_all_concurrent(
        client, STAT_SQL, merchants, start_ts, end_ts, "STAT", workers=10
    )

    # ── Step 3: 查询凭证账 (VOUCHER) ─────────────────────────────────────────
    print("\n=== Step 3: 查询凭证账 (order_line) ===")
    voucher_results, voucher_errors = query_all_concurrent(
        client, VOUCHER_SQL, merchants, start_ts, end_ts, "VOUCHER", workers=10
    )

    # ── Step 4: 查询支付 (payment_amount) ────────────────────────────────────
    print("\n=== Step 4: 查询支付 (sale_bill.payment_amount) ===")
    payment_results, payment_errors = query_all_concurrent(
        client, PAYMENT_SQL, merchants, start_ts, end_ts, "PAYMENT", workers=10
    )

    # ── Step 5: 查询外卖勾稽 (TIEOUT) ────────────────────────────────────────
    print("\n=== Step 5: 查询外卖订单勾稽 (takeout_tieout) ===")
    tieout_results, tieout_errors = query_all_concurrent(
        client, TIEOUT_SQL, merchants_with_takeout, start_ts, end_ts, "TIEOUT", workers=10
    )

    # ── Step 6: 跨账本互证分析 ───────────────────────────────────────────────
    print("\n=== Step 6: 跨账本互证分析 ===\n")

    all_cl_rows = []
    store_summary = []  # (store_num, store_name, n_items, qty_exact, gross_p95, worst_item_delta)
    per_store_errors = {}
    per_store_errors.update(stat_errors)
    per_store_errors.update({k: v for k, v in voucher_errors.items() if k not in per_store_errors})

    for _, uuid_str, store_num, store_name in merchants:
        if uuid_str in stat_errors and uuid_str in voucher_errors:
            continue  # both failed, already noted
        stat_rows = stat_results.get(uuid_str, [])
        voucher_rows = voucher_results.get(uuid_str, [])
        cl_rows = build_cross_ledger_rows(stat_rows, voucher_rows, store_num, store_name)
        all_cl_rows.extend(cl_rows)

        # Per-store qty exact match
        n_items = len(cl_rows)
        qty_exact = sum(1 for r in cl_rows if abs(r["stat_qty"] - r["voucher_qty"]) == 0)
        gross_deltas = sorted(abs(r["stat_gross"] - r["voucher_gross"]) for r in cl_rows)
        gp95 = percentile(gross_deltas, 95) if gross_deltas else 0.0
        worst = max((abs(r["stat_gross"] - r["voucher_gross"]) for r in cl_rows), default=0.0)
        store_summary.append((store_num, store_name, n_items, qty_exact, gp95, worst))

    # Global stats
    qty_stats = delta_stats(all_cl_rows, "stat_qty", "voucher_qty")
    gross_stats = delta_stats(all_cl_rows, "stat_gross", "voucher_gross")

    total_items = qty_stats["total"]
    qty_exact_count = total_items - qty_stats["nonzero"]
    qty_match_pct = (qty_exact_count / total_items * 100) if total_items else 0.0

    print(f"全局 (store,item) 行数: {total_items}")
    print(f"qty 精确匹配: {qty_exact_count}/{total_items} ({qty_match_pct:.1f}%)")
    print(f"qty |delta| 分布: P50={qty_stats['p50']:.0f}  P95={qty_stats['p95']:.0f}  max={qty_stats['max']:.0f}")
    print(f"gross |delta| 分布: P50={gross_stats['p50']:.2f}  P95={gross_stats['p95']:.2f}  max={gross_stats['max']:.2f}")

    # Run CROSS_LEDGER_IDENTITIES validator
    print()
    cl_result = check(all_cl_rows, CROSS_LEDGER_IDENTITIES)
    print_result(
        cl_result,
        row_label=lambda r: f"店{r['store_num']:>3} item={r['item_uuid'][:12]}",
        top_n_review=10,
    )

    # TOP 20 worst (store, item) by gross delta
    top20 = sorted(all_cl_rows, key=lambda r: abs(r["stat_gross"] - r["voucher_gross"]), reverse=True)[:20]
    print("\n--- TOP 20 最大 gross 差异 (stat vs voucher) ---")
    print(f"{'店':>4}  {'item_uuid':>16}  {'stat_qty':>9}  {'vchr_qty':>9}  {'stat_gross':>12}  {'vchr_gross':>12}  {'delta':>10}")
    for r in top20:
        delta = r["stat_gross"] - r["voucher_gross"]
        print(f"{r['store_num']:>4}  {r['item_uuid']:>16}  {r['stat_qty']:>9.0f}  {r['voucher_qty']:>9.0f}  {r['stat_gross']:>12.2f}  {r['voucher_gross']:>12.2f}  {delta:>+10.2f}")

    # Worst offender stores (by max gross delta)
    store_summary.sort(key=lambda x: x[5], reverse=True)
    print("\n--- 最差门店 Top 15 (按 max gross delta 排序) ---")
    print(f"{'店':>4}  {'名称':>20}  {'items':>6}  {'qty_exact':>10}  {'gross_P95':>10}  {'worst_delta':>12}")
    for row in store_summary[:15]:
        snum, sname, n, qe, gp95, worst = row
        print(f"{snum:>4}  {sname:>20}  {n:>6}  {qe:>10}/{n:<5}  {gp95:>10.2f}  {worst:>12.2f}")

    # ── Step 7: 支付勾稽分析 ─────────────────────────────────────────────────
    print("\n=== Step 7: 支付勾稽 (sale_bill.payment_amount vs 统计账实收) ===\n")

    payment_rows = []
    for _, uuid_str, store_num, store_name in merchants:
        pay_rows_bq = payment_results.get(uuid_str, [])
        pay_sum = float(pay_rows_bq[0].payment_amount_sum) if pay_rows_bq and pay_rows_bq[0].payment_amount_sum is not None else 0.0

        stat_rows_bq = stat_results.get(uuid_str, [])
        stat_actual = sum(float(r.stat_actual) for r in stat_rows_bq if r.stat_actual is not None)

        payment_rows.append({
            "store_num": store_num,
            "store_name": store_name,
            "uuid_str": uuid_str,
            "payment_amount_sum": pay_sum,
            "stat_actual_sum": stat_actual,
        })

    pay_result = check(payment_rows, [PAYMENT_TIEOUT_IDENTITY])
    print_result(
        pay_result,
        row_label=lambda r: f"店{r['store_num']:>3} {r['store_name'][:20]}",
        top_n_review=15,
    )

    # Payment delta table sorted by |delta|
    payment_rows_sorted = sorted(
        payment_rows,
        key=lambda r: abs(r["payment_amount_sum"] - r["stat_actual_sum"]),
        reverse=True
    )
    print("\n--- 支付勾稽 per-store delta (按 |delta| 排序, top 20) ---")
    print(f"{'店':>4}  {'名称':>20}  {'payment_sum':>14}  {'stat_actual':>14}  {'delta':>12}  {'delta%':>8}")
    for r in payment_rows_sorted[:20]:
        delta = r["payment_amount_sum"] - r["stat_actual_sum"]
        rel = (delta / r["stat_actual_sum"] * 100) if r["stat_actual_sum"] else 0.0
        print(f"{r['store_num']:>4}  {r['store_name']:>20}  {r['payment_amount_sum']:>14,.2f}  {r['stat_actual_sum']:>14,.2f}  {delta:>+12,.2f}  {rel:>+7.1f}%")

    # ── Step 8: 外卖勾稽分析 ─────────────────────────────────────────────────
    print("\n=== Step 8: 外卖订单勾稽 (takeout_tieout) ===\n")

    all_tieout_rows = []
    for _, uuid_str, store_num, store_name in merchants_with_takeout:
        t_rows = tieout_results.get(uuid_str, [])
        for r in t_rows:
            all_tieout_rows.append({
                "store_num": store_num,
                "store_name": store_name,
                "order_uuid": str(r.order_uuid),
                "order_state": int(r.order_state),
                "platform_total": float(r.platform_total),
                "merchant_charge_fee": float(r.merchant_charge_fee),
                "merchant_discount": float(r.merchant_discount),
                "item_sum": float(r.item_sum),
            })

    if all_tieout_rows:
        tieout_result = check(all_tieout_rows, TAKEOUT_TIEOUT_IDENTITIES)
        print_result(
            tieout_result,
            row_label=lambda r: f"店{r['store_num']:>3} order={r['order_uuid'][:12]}",
            top_n_review=10,
        )

        # Merchant fields all-zero check
        merchant_fee_nonzero = sum(1 for r in all_tieout_rows if r["merchant_charge_fee"] != 0.0)
        merchant_disc_nonzero = sum(1 for r in all_tieout_rows if r["merchant_discount"] != 0.0)
        total_orders = len(all_tieout_rows)
        match_count = sum(
            1 for r in all_tieout_rows
            if abs(r["platform_total"] - (r["item_sum"] - r["merchant_charge_fee"] - r["merchant_discount"])) < 0.01
        )
        print(f"外卖订单总数 (有表门店): {total_orders}")
        print(f"platform_total 匹配 (|delta|<0.01): {match_count}/{total_orders} ({match_count/total_orders*100:.1f}%)")
        print(f"merchant_charge_fee 非零行: {merchant_fee_nonzero}")
        print(f"merchant_discount 非零行: {merchant_disc_nonzero}")
        merchant_all_zero = (merchant_fee_nonzero == 0 and merchant_disc_nonzero == 0)
        print(f"merchant 两字段均恒 0: {'是 ✅ (口径假设暂时成立)' if merchant_all_zero else '否 ⚠️ 需校准符号'}")
    else:
        print("  (无外卖勾稽数据)")
        total_orders = 0
        match_count = 0
        merchant_all_zero = True

    # ── Step 9: N/A 门店清单 ──────────────────────────────────────────────────
    print("\n=== Step 9: 缺 ttpos_takeout_order 表门店 (N/A 清单) ===\n")
    for snum, sname, uuid_str in sorted(no_takeout, key=lambda x: x[0]):
        print(f"  店{snum:>3}  {sname:<20}  uuid={uuid_str}")

    # ── Step 10: 错误汇总 ────────────────────────────────────────────────────
    all_errors = {}
    all_errors.update({f"STAT:{k}": v for k, v in stat_errors.items()})
    all_errors.update({f"VOUCHER:{k}": v for k, v in voucher_errors.items()})
    all_errors.update({f"PAYMENT:{k}": v for k, v in payment_errors.items()})
    all_errors.update({f"TIEOUT:{k}": v for k, v in tieout_errors.items()})

    if all_errors:
        print(f"\n=== 错误汇总 ({len(all_errors)} 条) ===")
        for key, err in all_errors.items():
            print(f"  {key}: {err[:120]}")

    # ── 最终决策摘要 ─────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("最终决策摘要")
    print(f"{'='*60}")
    print(f"qty 匹配率:      {qty_match_pct:.1f}%  ({qty_exact_count}/{total_items})")
    print(f"gross delta P95: {gross_stats['p95']:.2f} THB")
    print(f"gross delta max: {gross_stats['max']:.2f} THB")
    pay_deltas = sorted(
        abs(r["payment_amount_sum"] - r["stat_actual_sum"]) for r in payment_rows
    )
    pay_p95 = percentile(pay_deltas, 95)
    pay_max = pay_deltas[-1] if pay_deltas else 0.0
    print(f"支付 delta P95:  {pay_p95:.2f} THB")
    print(f"支付 delta max:  {pay_max:.2f} THB")
    if all_tieout_rows:
        tieout_match_pct = match_count / total_orders * 100
    else:
        tieout_match_pct = 0.0
    print(f"外卖勾稽匹配率:  {tieout_match_pct:.1f}%  (有表门店 {len(has_takeout)} 店, {total_orders} 单)")
    print(f"merchant 字段恒0: {'是' if merchant_all_zero else '否'}")
    print(f"缺外卖表门店:    {len(no_takeout)} 店")
    print(f"查询错误总数:    {len(all_errors)} 条")

    if qty_match_pct == 100.0 and len(all_errors) == 0:
        print("\n[决策] qty 匹配率 100% → CROSS_LEDGER_QTY 可考虑升入闸门 (核实 gross 和 errors 后)")
    elif qty_match_pct >= 95.0:
        print("\n[决策] qty 存在稳定可解释差异 (时间语义/退款时点) → 修 order_line 口径后复跑")
    else:
        print("\n[决策] qty 差异超预期 → 开专项排查, CROSS_LEDGER 维持观察模式")

    print(f"\n完成. 全量输出见 /tmp/cross_ledger_obs.txt")


if __name__ == "__main__":
    main()
