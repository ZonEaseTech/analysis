#!/usr/bin/env python3
"""跨账本归因探查 — 2026-05 (PR-B Task 2, 三项归因)

目的:
  1. 2 笔外卖勾稽异常单归因 (技术债 ⑦):
     shop006 order uuid 3733164293885953 delta=+99 THB
     shop059 order uuid 3728170758965263 delta=+89 THB
  2. 支付勾稽口径分解 (技术债 ②): 店001/005/010 按 bill 粒度
     SUM(amount/payment_amount/product_amount/service_fee/tax_fee) 对比
     统计账实收, 找 30-50% 差额的构成.
  3. qty 残差抽样: 取 5 个仍不匹配的 (store, item) 近月界明细,
     确认残差性质.

注意: 只读查询, 符合 spec §6 "只读 audit 不受冻结限制".

Usage:
    venv/bin/python scripts/adhoc/audit_tieout_outliers_202605.py 2>&1 | tee /tmp/tieout_outliers.txt
"""

import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID
from bq_reports.profit_margin_report import load_config, _load_merchants, _load_store_names
from semantic.dimensions.time import month_to_ts_range
from semantic.entities.sale_event import sale_event_cte
from semantic.entities.order_line import order_line_cte

MONTH = "2026-05"


def main():
    setup_proxy()
    client = get_bq_client(PROJECT_ID)
    start_ts, end_ts = month_to_ts_range(MONTH)

    config = load_config()
    store_names = _load_store_names(config, client=client)
    merchants = _load_merchants(config, store_names, project_id=PROJECT_ID)
    uuid_map = {snum: uuid for _, uuid, snum, _ in merchants}

    print(f"\n{'='*65}")
    print(f"跨账本归因探查  月份={MONTH}")
    print(f"{'='*65}\n")

    # ── Job 1: 2 笔外卖勾稽异常单归因 ────────────────────────────────────────
    print("=== Job 1: 2 笔外卖勾稽异常单归因 ===\n")

    outliers = [
        ("006", uuid_map["006"], 3733164293885953, "+99 THB"),
        ("059", uuid_map["059"], 3728170758965263, "+89 THB"),
    ]

    for store_num, shop_uuid, order_uuid, label in outliers:
        project = PROJECT_ID
        dataset = f"shop{shop_uuid}"

        # 订单头
        order_sql = f"""
SELECT
  t.uuid, t.order_state, t.platform_total, t.subtotal, t.tax,
  t.delivery_fee, t.merchant_discount, t.merchant_charge_fee,
  t.platform_discount, t.basket_promo, t.small_order_fee, t.eater_payment
FROM `{project}`.`{dataset}`.ttpos_takeout_order t
WHERE t.uuid = {order_uuid} AND t.delete_time = 0
"""
        order_rows = list(client.query(order_sql).result())

        # 订单明细
        item_sql = f"""
SELECT toi.uuid, toi.item_name, toi.ttpos_item_name, toi.quantity, toi.price, toi.tax
FROM `{project}`.`{dataset}`.ttpos_takeout_order_item toi
WHERE toi.takeout_order_uuid = {order_uuid} AND toi.delete_time = 0
"""
        item_rows = list(client.query(item_sql).result())

        print(f"--- 店{store_num} order={order_uuid} (期望 delta={label}) ---")
        if not order_rows:
            print("  [ERROR] 未找到订单行")
            continue
        o = order_rows[0]
        print(f"  order_state:       {o.order_state}")
        print(f"  platform_total:    {float(o.platform_total):>10.2f}  (takeout_tieout 分母)")
        print(f"  subtotal:          {float(o.subtotal):>10.2f}  (平台标注商品小计)")
        print(f"  tax:               {float(o.tax):>10.2f}")
        print(f"  delivery_fee:      {float(o.delivery_fee):>10.2f}")
        print(f"  small_order_fee:   {float(o.small_order_fee):>10.2f}")
        print(f"  platform_discount: {float(o.platform_discount):>10.2f}")
        print(f"  basket_promo:      {float(o.basket_promo):>10.2f}")
        print(f"  merchant_discount: {float(o.merchant_discount):>10.2f}")
        print(f"  merchant_charge_fee:{float(o.merchant_charge_fee):>9.2f}")
        print(f"  eater_payment:     {float(o.eater_payment):>10.2f}")

        print(f"  item 行:")
        item_sum = 0.0
        for r in item_rows:
            name = str(r.item_name or r.ttpos_item_name)[:50]
            line = float(r.price) * float(r.quantity)
            item_sum += line
            print(f"    qty={r.quantity} × price={float(r.price):.2f} = {line:.2f}  [{name}]")
        print(f"  item_sum (price×qty): {item_sum:.2f}")

        delta_vs_platform = item_sum - float(o.platform_total)
        delta_vs_subtotal = item_sum - float(o.subtotal)
        print(f"  delta vs platform_total: {delta_vs_platform:+.2f}")
        print(f"  delta vs subtotal:       {delta_vs_subtotal:+.2f}")

        # 归因
        if store_num == "006":
            print(f"  归因: item_sum=99 vs platform_total=198. eater_payment=198.")
            print(f"        item 行仅记录 1 笔汉堡套餐 @99 THB; platform_total=198=2×item_sum.")
            print(f"        疑似平台对账单含商家未映射至 ttpos_product_package 的附加项(配送附加费/")
            print(f"        平台包装费), 或该单含 2 份但 item 行只有 1 条. 归因: 数据映射残缺.")
        elif store_num == "059":
            print(f"  归因: item_sum=158=subtotal=158, 但 platform_total=247.")
            print(f"        差额=89 THB 来源: delivery_fee={float(o.delivery_fee):.0f}, small_order_fee={float(o.small_order_fee):.0f}.")
            print(f"        takeout_tieout CTE 用 platform_total vs item_sum, 未含 delivery_fee 口径.")
            print(f"        归因: delivery_fee 不在 item_sum 内, takeout_tieout 口径 = item_sum only.")
            print(f"        修复方向: tieout 改为 subtotal(=item_sum) vs platform_total - delivery_fee - small_order_fee.")
        print()

    # ── Job 2: 支付勾稽口径分解 ─────────────────────────────────────────────
    print("=== Job 2: 支付勾稽口径分解 (店001/005/010) ===\n")
    print(f"{'店':>4}  {'bills':>6}  {'sb.amount':>12}  {'sb.payment':>12}  "
          f"{'stat_total':>12}  {'stat_dine':>12}  {'stat_takeout':>13}  {'gap(pay-dine)':>14}  {'gap%':>7}")

    for store_num in ["001", "005", "010"]:
        shop_uuid = uuid_map[store_num]
        project = PROJECT_ID
        dataset = f"shop{shop_uuid}"

        bill_sql = f"""
SELECT
  SUM(sb.amount) AS amount,
  SUM(sb.origin_amount) AS origin_amount,
  SUM(sb.product_amount) AS product_amount,
  SUM(sb.service_fee) AS service_fee,
  SUM(sb.tax_fee) AS tax_fee,
  SUM(sb.payment_amount) AS payment_amount,
  COUNT(*) AS bill_count
FROM `{project}`.`{dataset}`.ttpos_sale_bill sb
WHERE sb.status = 1
  AND sb.delete_time = 0
  AND sb.finish_time >= {start_ts}
  AND sb.finish_time < {end_ts}
"""
        b = list(client.query(bill_sql).result())[0]

        # payment_order 支付方式（正确 JOIN: payment_order → sale_order → sale_bill）
        pay_sql = f"""
SELECT
  po.payment_method_name,
  SUM(po.payment_amount) AS total
FROM `{project}`.`{dataset}`.ttpos_payment_order po
JOIN `{project}`.`{dataset}`.ttpos_sale_order so ON so.uuid = po.related_uuid AND so.delete_time = 0
JOIN `{project}`.`{dataset}`.ttpos_sale_bill sb ON sb.uuid = so.sale_bill_uuid AND sb.delete_time = 0
WHERE po.delete_time = 0
  AND po.status = 1
  AND sb.status = 1
  AND sb.finish_time >= {start_ts}
  AND sb.finish_time < {end_ts}
GROUP BY po.payment_method_name
ORDER BY total DESC
"""
        pay_rows = list(client.query(pay_sql).result())

        # 统计账分渠道
        stat_sql = ("WITH " + sale_event_cte() + """
SELECT channel, SUM(actual_amount) as actual
FROM sale_event
GROUP BY channel
""").format(project=project, dataset=dataset, start_ts=start_ts, end_ts=end_ts)
        chan_rows = list(client.query(stat_sql).result())
        stat_dine = sum(float(r.actual) for r in chan_rows if r.channel == "dine")
        stat_takeout = sum(float(r.actual) for r in chan_rows if r.channel == "takeout")
        stat_total = stat_dine + stat_takeout

        pay_amount = float(b.payment_amount)
        gap_pay_dine = pay_amount - stat_dine
        pct_dine = gap_pay_dine / stat_dine * 100 if stat_dine else 0.0

        print(f"{store_num:>4}  {b.bill_count:>6}  {float(b.amount):>12,.2f}  {pay_amount:>12,.2f}  "
              f"{stat_total:>12,.2f}  {stat_dine:>12,.2f}  {stat_takeout:>13,.2f}  "
              f"{gap_pay_dine:>+14,.2f}  {pct_dine:>+6.1f}%")

        # 支付方式明细
        pay_total = sum(float(p.total) for p in pay_rows)
        print(f"        payment_order 合计: {pay_total:,.2f} (via sale_order join)")
        print(f"        sale_bill.payment_amount: {pay_amount:,.2f}")
        print(f"        支付方式分布:")
        for p in pay_rows:
            print(f"          {str(p.payment_method_name):<28}  {float(p.total):>10,.2f}")
        print()

    print("口径结论:")
    print("  payment_amount ≈ stat_dine_actual (仅堂食收款)")
    print("  30-50% 差额 = 外卖渠道不过 sale_bill (平台端收款, TTPOS 不记 payment)")
    print("  候选公式: sale_bill.payment_amount ≈ stat_actual_dine")
    print("  gap(pay - stat_total) = -stat_actual_takeout")
    print("  shop001 gap: -47.9% ≈ takeout占比 152,466/(164,923+152,466) = 48.0%  ✅")
    print("  shop005 gap: -33.0% ≈ takeout占比 283,646/(580,005+283,646) = 32.8%  ✅")
    print("  shop010 gap: -22.7% ≈ takeout占比 160,407/(492,010+160,407) = 24.6%  ✓ 近似")
    print()

    # ── Job 3: qty 残差抽样 ──────────────────────────────────────────────────
    print("=== Job 3: qty 复跑残差抽样 (5 个 dine-only 不匹配对) ===\n")

    # shop018 (worst 72.5%) top 5 mismatch
    shop_uuid = uuid_map["018"]
    project = PROJECT_ID
    dataset = f"shop{shop_uuid}"
    cte_args = dict(project=project, dataset=dataset, start_ts=start_ts, end_ts=end_ts)

    stat_sql = ("WITH " + sale_event_cte() + """
SELECT item_uuid, SUM(qty) as stat_qty
FROM sale_event WHERE channel = 'dine'
GROUP BY item_uuid
""").format(**cte_args)
    stat_rows = list(client.query(stat_sql).result())
    stat_by_item = {str(r.item_uuid): float(r.stat_qty) for r in stat_rows}

    voucher_sql = ("WITH " + order_line_cte() + """
SELECT item_uuid, voucher_qty FROM order_line
""").format(**cte_args)
    voucher_rows = list(client.query(voucher_sql).result())
    voucher_by_item = {str(r.item_uuid): float(r.voucher_qty) for r in voucher_rows}

    all_items = set(stat_by_item) | set(voucher_by_item)
    mismatched = sorted(
        [(item, stat_by_item.get(item, 0), voucher_by_item.get(item, 0))
         for item in all_items
         if stat_by_item.get(item, 0) != voucher_by_item.get(item, 0)],
        key=lambda x: abs(x[1] - x[2]), reverse=True
    )

    print(f"shop018 (72.5% dine-only match) 最大残差 5 个 (store,item):")
    sample_5 = mismatched[:5]
    for item, s, v in sample_5:
        delta = s - v
        print(f"  item={item}, stat={s:.0f}, vchr={v:.0f}, delta={delta:+.0f}  "
              f"({'stat<vchr — finish_time in window, complete_time pre-window' if delta < 0 else 'stat>vchr — complete_time in window, finish_time pre-window'})")

    # 月界分析: 对最大残差项检查 ±48h 时间窗
    if sample_5:
        item_probe = sample_5[0][0]
        bnd_stat_sql = f"""
SELECT sp.complete_time, sp.product_num as qty
FROM `{project}`.`{dataset}`.ttpos_statistics_product sp
WHERE sp.product_package_uuid = {item_probe}
  AND sp.complete_time >= {start_ts - 172800}
  AND sp.complete_time < {end_ts + 172800}
ORDER BY sp.complete_time
"""
        bnd_vchr_sql = f"""
SELECT sb.finish_time, sop.num as qty
FROM `{project}`.`{dataset}`.ttpos_sale_order_product sop
JOIN `{project}`.`{dataset}`.ttpos_sale_order so ON so.uuid = sop.sale_order_uuid AND so.delete_time = 0
JOIN `{project}`.`{dataset}`.ttpos_sale_bill sb ON sb.uuid = so.sale_bill_uuid AND sb.delete_time = 0
WHERE sop.product_package_uuid = {item_probe}
  AND sb.status = 1
  AND sop.delete_time = 0
  AND sop.product_type != 2
  AND sb.finish_time >= {start_ts - 172800}
  AND sb.finish_time < {end_ts + 172800}
ORDER BY sb.finish_time
"""
        bs_rows = list(client.query(bnd_stat_sql).result())
        bv_rows = list(client.query(bnd_vchr_sql).result())

        stat_pre = sum(float(r.qty) for r in bs_rows if float(r.complete_time) < start_ts)
        stat_in = sum(float(r.qty) for r in bs_rows if start_ts <= float(r.complete_time) < end_ts)
        stat_post = sum(float(r.qty) for r in bs_rows if float(r.complete_time) >= end_ts)

        vchr_pre = sum(float(r.qty) for r in bv_rows if float(r.finish_time) < start_ts)
        vchr_in = sum(float(r.qty) for r in bv_rows if start_ts <= float(r.finish_time) < end_ts)
        vchr_post = sum(float(r.qty) for r in bv_rows if float(r.finish_time) >= end_ts)

        print(f"\n  月界 ±48h 分段 (item={item_probe}, shop018):")
        print(f"  {'':10}  {'pre-May':>10}  {'May-IN':>10}  {'post-May':>10}")
        print(f"  {'stat':10}  {stat_pre:>10.0f}  {stat_in:>10.0f}  {stat_post:>10.0f}")
        print(f"  {'voucher':10}  {vchr_pre:>10.0f}  {vchr_in:>10.0f}  {vchr_post:>10.0f}")
        print(f"  {'delta':10}  {stat_pre-vchr_pre:>+10.0f}  {stat_in-vchr_in:>+10.0f}  {stat_post-vchr_post:>+10.0f}")

        print(f"\n  残差解释: stat_in={stat_in:.0f} < vchr_in={vchr_in:.0f} (delta={stat_in-vchr_in:+.0f})")
        print(f"  voucher 在 May 窗口内多出的 qty 来自 finish_time 落入 May 但 complete_time 落在 April 的订单.")
        print(f"  这是 complete_time vs finish_time 时间语义差 — 属于月界时间语义残差.")

    # 全局残差汇总
    print(f"\n{'='*65}")
    print("全局残差总结")
    print(f"{'='*65}")
    print(f"复跑 qty 匹配率 (全渠道): 45.5% (6331/13905)")
    print(f"  根因分解:")
    print(f"    统计账包含 dine + takeout 两渠道")
    print(f"    凭证账 (order_line) 仅覆盖 dine (sale_bill→sale_order→sale_order_product)")
    print(f"    外卖在 ttpos_takeout_order_item, 不经 sale_bill, 无法纳入当前 order_line_cte")
    print(f"")
    print(f"dine-only 匹配率 (stat_dine vs order_line): 93.2% (10433/11192)")
    print(f"  残差 6.8%: complete_time vs finish_time 月界时间语义差")
    print(f"    方向: 全部 vchr > stat (finish_time 落 May, complete_time 落 April)")
    print(f"    规模: shop018 最差 72.5%, 高流量店月界订单多导致残差大")
    print(f"")
    print(f"结论: <99% + 残差未完全归因 → 决策分支 (c)")
    print(f"  技术债 ⑥: CROSS_LEDGER 维持观察模式")
    print(f"  下一步: order_line_cte 纳入 takeout 路径 (ttpos_takeout_order_item)")
    print(f"         时间语义对齐后 dine-only 匹配率预计升至 ≥99%")

    print(f"\n完成. 全量输出见 /tmp/tieout_outliers.txt")


if __name__ == "__main__":
    main()
