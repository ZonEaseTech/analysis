#!/usr/bin/env python3
# 谁问的: 何伟涛 / 2026-05-28
# 问什么: statistics_payment.payment_amount 是否已扣退款/作废 (毛额 vs 净额)
# 结论: payment_amount 是毛额(全正,无负行),退款单独记在 refund_amount 列;
#        真实收 net = SUM(payment_amount) - SUM(refund_amount),并应剔除关联 status=2(已取消)账单的支付
"""遍历所有 TH 门店,统计 ttpos_statistics_payment 在 2026-04 的:
   1) payment_amount 符号分布(正/负/零)
   2) refund_amount 列分布
   3) 关联 ttpos_sale_bill.status=2(已取消)账单的 payment_amount 合计
   4) 给出真实收口径结论与金额。
"""
import sys
sys.path.insert(0, '/home/weifashi/hwt/analysis')

from bq_reports.utils.bq_client import get_bq_client

client = get_bq_client()
BQ_LOCATION = "asia-southeast1"

START_TS = 1774976400
END_TS = 1777568400

# ========== 门店枚举 ==========
job = client.query('''
    SELECT c.uuid
    FROM `diyl-407103`.`saas`.`ttpos_company` c
    LEFT JOIN `diyl-407103`.`saas`.`ttpos_company_setting` cs
      ON cs.company_uuid = c.uuid AND cs.delete_time = 0
    WHERE c.delete_time = 0
      AND cs.headquarter_uuid = 5080409448448000
      AND cs.erpnext_company_abbr LIKE 'TH%'
    ORDER BY cs.erpnext_company_abbr
''', location=BQ_LOCATION)
all_uuids = [str(r.uuid) for r in job.result()]

datasets = list(client.list_datasets())
shop_ids = set(d.dataset_id for d in datasets if d.dataset_id.startswith('shop'))
stores = [f"shop{u}" for u in all_uuids if f"shop{u}" in shop_ids]
print(f"TH 门店(配置) {len(all_uuids)}, 实际存在 shop* dataset {len(stores)}")

# ========== 累加器 ==========
agg = {
    'pos_pay': 0.0,            # SUM(payment_amount) 全量(毛)
    'pos_cnt': 0,             # 笔数
    'pay_pos_sum': 0.0,       # payment_amount > 0 合计
    'pay_pos_cnt': 0,
    'pay_neg_sum': 0.0,       # payment_amount < 0 合计
    'pay_neg_cnt': 0,
    'pay_zero_cnt': 0,        # payment_amount = 0
    'refund_sum': 0.0,        # SUM(refund_amount)
    'refund_pos_cnt': 0,      # refund_amount > 0 笔数
    'refund_neg_sum': 0.0,    # refund_amount < 0 合计(异常)
    'refund_neg_cnt': 0,
    'cancelled_pay': 0.0,     # 关联 status=2 账单的 payment_amount 合计
    'cancelled_cnt': 0,
    'cancelled_refund': 0.0,  # 关联 status=2 账单的 refund_amount 合计
}
ok = fail = 0
fail_ds = []

for ds in stores:
    try:
        # --- A) payment_amount / refund_amount 符号分布 ---
        job = client.query(f"""
            SELECT
              COUNT(*) AS cnt,
              SUM(payment_amount) AS pay_all,
              SUM(IF(payment_amount > 0, payment_amount, 0)) AS pay_pos,
              COUNTIF(payment_amount > 0) AS pay_pos_cnt,
              SUM(IF(payment_amount < 0, payment_amount, 0)) AS pay_neg,
              COUNTIF(payment_amount < 0) AS pay_neg_cnt,
              COUNTIF(payment_amount = 0) AS pay_zero_cnt,
              SUM(refund_amount) AS refund_all,
              COUNTIF(refund_amount > 0) AS refund_pos_cnt,
              SUM(IF(refund_amount < 0, refund_amount, 0)) AS refund_neg,
              COUNTIF(refund_amount < 0) AS refund_neg_cnt
            FROM `diyl-407103`.`{ds}`.`ttpos_statistics_payment`
            WHERE delete_time = 0
              AND complete_time >= {START_TS} AND complete_time < {END_TS}
        """, location=BQ_LOCATION)
        r = list(job.result())[0]
        agg['pos_pay'] += float(r.pay_all or 0)
        agg['pos_cnt'] += int(r.cnt or 0)
        agg['pay_pos_sum'] += float(r.pay_pos or 0)
        agg['pay_pos_cnt'] += int(r.pay_pos_cnt or 0)
        agg['pay_neg_sum'] += float(r.pay_neg or 0)
        agg['pay_neg_cnt'] += int(r.pay_neg_cnt or 0)
        agg['pay_zero_cnt'] += int(r.pay_zero_cnt or 0)
        agg['refund_sum'] += float(r.refund_all or 0)
        agg['refund_pos_cnt'] += int(r.refund_pos_cnt or 0)
        agg['refund_neg_sum'] += float(r.refund_neg or 0)
        agg['refund_neg_cnt'] += int(r.refund_neg_cnt or 0)

        # --- B) 关联 status=2(已取消)账单 ---
        # sale_bill.uuid 是 INT64, statistics_payment.sale_bill_uuid 是 NUMERIC(20) -> 转 string 关联
        job = client.query(f"""
            SELECT
              SUM(sp.payment_amount) AS cancelled_pay,
              COUNT(*) AS cancelled_cnt,
              SUM(sp.refund_amount) AS cancelled_refund
            FROM `diyl-407103`.`{ds}`.`ttpos_statistics_payment` sp
            JOIN `diyl-407103`.`{ds}`.`ttpos_sale_bill` sb
              ON CAST(sb.uuid AS STRING) = CAST(sp.sale_bill_uuid AS STRING)
            WHERE sp.delete_time = 0
              AND sp.complete_time >= {START_TS} AND sp.complete_time < {END_TS}
              AND sb.status = 2
        """, location=BQ_LOCATION)
        r2 = list(job.result())[0]
        agg['cancelled_pay'] += float(r2.cancelled_pay or 0)
        agg['cancelled_cnt'] += int(r2.cancelled_cnt or 0)
        agg['cancelled_refund'] += float(r2.cancelled_refund or 0)
        ok += 1
    except Exception as e:
        fail += 1
        fail_ds.append((ds, str(e)[:80]))

print(f"查询成功 {ok} 店, 失败/缺表 {fail} 店")
for ds, e in fail_ds:
    print(f"  FAIL {ds}: {e}")

def fmt(x):
    return f"{x:,.2f}"

print("\n" + "=" * 70)
print("1) payment_amount 符号分布 (2026-04 全门店)")
print("=" * 70)
print(f"  总笔数                  {agg['pos_cnt']:>14,}")
print(f"  SUM(payment_amount) 毛  {fmt(agg['pos_pay']):>18}")
print(f"    > 0  合计 / 笔数      {fmt(agg['pay_pos_sum']):>18}  ({agg['pay_pos_cnt']:,} 笔)")
print(f"    < 0  合计 / 笔数      {fmt(agg['pay_neg_sum']):>18}  ({agg['pay_neg_cnt']:,} 笔)")
print(f"    = 0  笔数            {agg['pay_zero_cnt']:>18,}")

print("\n" + "=" * 70)
print("2) refund_amount 列分布")
print("=" * 70)
print(f"  SUM(refund_amount)       {fmt(agg['refund_sum']):>18}")
print(f"    > 0 笔数              {agg['refund_pos_cnt']:>18,}")
print(f"    < 0 合计 / 笔数(异常) {fmt(agg['refund_neg_sum']):>18}  ({agg['refund_neg_cnt']:,} 笔)")

print("\n" + "=" * 70)
print("3) 关联 ttpos_sale_bill.status=2 (已取消) 账单")
print("=" * 70)
print(f"  payment_amount 合计      {fmt(agg['cancelled_pay']):>18}  ({agg['cancelled_cnt']:,} 笔)")
print(f"  其中 refund_amount 合计  {fmt(agg['cancelled_refund']):>18}")

print("\n" + "=" * 70)
print("4) 真实收口径推演")
print("=" * 70)
gross = agg['pos_pay']
refund = agg['refund_sum']
cancelled_net = agg['cancelled_pay'] - agg['cancelled_refund']  # 取消账单里尚未被 refund 抵掉的部分
print(f"  毛额 SUM(payment_amount)                 = {fmt(gross)}")
print(f"  减 SUM(refund_amount)                    = -{fmt(refund)}")
net_after_refund = gross - refund
print(f"  => net1 (扣退款)                         = {fmt(net_after_refund)}")
print(f"  取消账单(status=2) net 残留(pay-refund)  = {fmt(cancelled_net)}")
net2 = net_after_refund - cancelled_net
print(f"  => net2 (再剔除取消账单残留)             = {fmt(net2)}")
print(f"\n  vs 之前毛额直接 SUM 与客户表差约 14 万")
print(f"  扣退款带来的下修 = {fmt(refund)}")
