"""对比 2026-02 堂食实收两个维度:
  A) statistics_product 维度 (商品行 SUM, 我们 v10 的口径)
  B) sale_bill 维度 (账单层 SUM, ttpos 后台另一类报表口径)
看 ~13,837 差异是否来自这两个口径之差。"""
import sys, csv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID, STORE_UUIDS

setup_proxy(); c = get_bq_client()

with open('resources/wallace.20260515/order_excludes.csv', encoding='utf-8-sig') as f:
    dine_excl = set(int(r['exclude_key']) for r in csv.DictReader(f) if r['channel']=='dine')

S = "UNIX_SECONDS(TIMESTAMP('2026-02-01 00:00:00+07'))"
E = "UNIX_SECONDS(TIMESTAMP('2026-03-01 00:00:00+07'))"

# A) statistics_product 维度 = 我们 v10 口径
A_unions = []
for u in STORE_UUIDS:
    excl = f"AND sp.sale_order_uuid NOT IN ({','.join(map(str,dine_excl))})" if dine_excl else ""
    A_unions.append(f"""
SELECT IF(sp.free_num>0 OR sp.give_num>0, 0,
   sp.product_final_price*(sp.product_num-sp.refund_num)) AS amt
FROM `{PROJECT_ID}.shop{u}.ttpos_statistics_product` sp
WHERE sp.complete_time>={S} AND sp.complete_time<{E} {excl}""")
sql_A = f"WITH a AS ({' UNION ALL '.join(A_unions)}) SELECT ROUND(SUM(amt),2) v FROM a"

# B) sale_bill 维度
# ttpos 后台账单实收通常: SUM(sb.payment_amount) 或 SUM(sb.amount)
# 过滤条件参考 ttpos 后台报表: status=1 (正常成交), hide_bill_time=0, delete_time=0
# 时间窗用 finish_time (结账时间) 落在 2 月

B_unions = []
for u in STORE_UUIDS:
    B_unions.append(f"""
SELECT
  sb.amount        AS bill_amount,
  sb.payment_amount AS payment_amount,
  sb.product_amount AS product_amount,
  sb.origin_amount  AS origin_amount
FROM `{PROJECT_ID}.shop{u}.ttpos_sale_bill` sb
WHERE sb.finish_time >= {S} AND sb.finish_time < {E}
  AND sb.delete_time = 0
  AND sb.hide_bill_time = 0
  AND sb.status = 1
""")
sql_B = f"""
WITH a AS ({' UNION ALL '.join(B_unions)})
SELECT
  ROUND(SUM(bill_amount),2) AS bill_amount,
  ROUND(SUM(payment_amount),2) AS payment_amount,
  ROUND(SUM(product_amount),2) AS product_amount,
  ROUND(SUM(origin_amount),2)  AS origin_amount,
  COUNT(*) AS bill_count
FROM a
"""

# C) ttpos 后台 ExcludeTestBusinessByBillSQL 的等价: 排除 hide_bill_time, 也排测试营业期(本月无影响)
# 跟 B 应该一致, 但我也算下"含 hide_bill_time"的版本看看
C_unions = []
for u in STORE_UUIDS:
    C_unions.append(f"""
SELECT
  sb.amount, sb.payment_amount, sb.product_amount
FROM `{PROJECT_ID}.shop{u}.ttpos_sale_bill` sb
WHERE sb.finish_time >= {S} AND sb.finish_time < {E}
  AND sb.delete_time = 0
  AND sb.status = 1
""")
sql_C = f"""WITH a AS ({' UNION ALL '.join(C_unions)})
SELECT ROUND(SUM(amount),2) a, ROUND(SUM(payment_amount),2) p, ROUND(SUM(product_amount),2) pr FROM a"""

print("=== A) statistics_product 维度 (v10 堂食实收口径, 已 excludes) ===")
for r in c.query(sql_A).result():
    A = r['v']
    print(f"  {A:,.2f}")

print("\n=== B) sale_bill 维度 (finish_time 2 月, status=1, 不 hide, 不 delete) ===")
for r in c.query(sql_B).result():
    print(f"  bill_count        = {r['bill_count']:,}")
    print(f"  SUM(bill.amount)         = {r['bill_amount']:,.2f}    ← 账单总金额")
    print(f"  SUM(bill.payment_amount) = {r['payment_amount']:,.2f}  ← 账单实收")
    print(f"  SUM(bill.product_amount) = {r['product_amount']:,.2f}  ← 商品金额")
    print(f"  SUM(bill.origin_amount)  = {r['origin_amount']:,.2f}   ← 原价")
    print(f"\n  payment_amount - A (我们) = {r['payment_amount'] - A:,.2f}")
    print(f"  product_amount - A (我们) = {r['product_amount'] - A:,.2f}")

print("\n=== C) sale_bill 维度 (含 hide) ===")
for r in c.query(sql_C).result():
    print(f"  amount={r['a']:,.2f}  payment={r['p']:,.2f}  product={r['pr']:,.2f}")
