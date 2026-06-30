"""2026-02 外卖: 1) 平台手续费各字段合计; 2) is_abnormal=1 订单合计; 3) 平台拆分."""
import sys, csv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID, STORE_UUIDS

setup_proxy(); c = get_bq_client()

with open('resources/wallace.20260515/order_excludes.csv', encoding='utf-8-sig') as f:
    take_excl = set(int(r['exclude_key']) for r in csv.DictReader(f) if r['channel']=='takeout')

S = "UNIX_SECONDS(TIMESTAMP('2026-02-01 00:00:00+07'))"
E = "UNIX_SECONDS(TIMESTAMP('2026-03-01 00:00:00+07'))"
excl_clause = f"AND t.uuid NOT IN ({','.join(map(str,take_excl))})" if take_excl else ""

# 注意: v10 外卖实收 = SUM(toi.price * toi.quantity) for state IN (10..40)
# 我们要看 takeout_order 级别的字段, 但用 join 算金额一致性
unions = []
for u in STORE_UUIDS:
    unions.append(f"""
SELECT
  '{u}' AS store, t.platform, t.uuid, t.order_state, t.is_abnormal, t.abnormal_detail,
  t.subtotal, t.eater_payment, t.platform_total, t.delivery_fee, t.small_order_fee,
  t.platform_discount, t.merchant_discount, t.basket_promo, t.tax, t.merchant_charge_fee
FROM `{PROJECT_ID}.shop{u}.ttpos_takeout_order` t
WHERE t.delete_time = 0
  AND t.order_state IN (10,20,30,40,60)
  AND t.accepted_time > 0
  AND ((t.order_state=40 AND t.completed_time>={S} AND t.completed_time<{E})
       OR (t.order_state!=40 AND t.accepted_time>={S} AND t.accepted_time<{E}))
  {excl_clause}
""")

base = f"WITH r AS ({' UNION ALL '.join(unions)})"

# 总览
sql1 = base + """
SELECT
  ROUND(SUM(IF(order_state IN (10,20,30,40), subtotal, 0)),2) AS subtotal_kept,
  ROUND(SUM(IF(order_state IN (10,20,30,40), eater_payment, 0)),2) AS eater_payment,
  ROUND(SUM(IF(order_state IN (10,20,30,40), platform_total, 0)),2) AS platform_total,
  ROUND(SUM(IF(order_state IN (10,20,30,40), merchant_charge_fee, 0)),2) AS merchant_charge_fee,
  ROUND(SUM(IF(order_state IN (10,20,30,40), platform_discount, 0)),2) AS platform_discount,
  ROUND(SUM(IF(order_state IN (10,20,30,40), merchant_discount, 0)),2) AS merchant_discount,
  ROUND(SUM(IF(order_state IN (10,20,30,40), basket_promo, 0)),2) AS basket_promo,
  ROUND(SUM(IF(order_state IN (10,20,30,40), delivery_fee, 0)),2) AS delivery_fee,
  ROUND(SUM(IF(order_state IN (10,20,30,40), tax, 0)),2) AS tax
FROM r
"""
print("=== 2026-02 外卖各金额字段合计 (state IN 10/20/30/40, 已扣 14 单 excludes) ===")
for r in c.query(sql1).result():
    for k in r.keys():
        print(f"  {k:<25} {r[k] or 0:>15,.2f}")

# is_abnormal 分布
sql2 = base + """
SELECT
  is_abnormal,
  COUNT(*) AS n_orders,
  ROUND(SUM(IF(order_state IN (10,20,30,40), subtotal, 0)),2) AS subtotal_kept
FROM r GROUP BY is_abnormal ORDER BY is_abnormal
"""
print("\n=== 按 is_abnormal 分布 ===")
for r in c.query(sql2).result():
    print(f"  is_abnormal={r['is_abnormal']}  单数={r['n_orders']:,}  subtotal_kept={r['subtotal_kept'] or 0:,.2f}")

# 按 platform 拆
sql3 = base + """
SELECT
  platform,
  COUNT(*) AS n,
  ROUND(SUM(IF(order_state IN (10,20,30,40), subtotal, 0)),2) AS subtotal_kept,
  ROUND(SUM(IF(order_state IN (10,20,30,40), merchant_charge_fee, 0)),2) AS charge_fee
FROM r GROUP BY platform ORDER BY platform
"""
print("\n=== 按 platform 拆 ===")
for r in c.query(sql3).result():
    print(f"  {str(r['platform']):<15} 单数={r['n']:,}  subtotal={r['subtotal_kept'] or 0:,.2f}  charge_fee={r['charge_fee'] or 0:,.2f}")
