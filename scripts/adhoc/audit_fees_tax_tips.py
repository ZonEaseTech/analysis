"""穷举 2026-02 所有"乱七八糟"费用字段, 看哪个量级 ≈ ฿13,837。"""
import sys, csv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID, STORE_UUIDS

setup_proxy(); c = get_bq_client()

with open('resources/wallace.20260515/order_excludes.csv', encoding='utf-8-sig') as f:
    rows_csv = list(csv.DictReader(f))
dine_excl = set(int(r['exclude_key']) for r in rows_csv if r['channel']=='dine')
take_excl = set(int(r['exclude_key']) for r in rows_csv if r['channel']=='takeout')

S = "UNIX_SECONDS(TIMESTAMP('2026-02-01 00:00:00+07'))"
E = "UNIX_SECONDS(TIMESTAMP('2026-03-01 00:00:00+07'))"

# 看 statistics_product schema 有哪些 fee/tax 字段
print("=== statistics_product schema 里所有金额字段 ===")
t = c.get_table(f"{PROJECT_ID}.shop{STORE_UUIDS[0]}.ttpos_statistics_product")
for f in t.schema:
    n = f.name.lower()
    if any(k in n for k in ['fee','tax','tip','price','amount','discount']):
        print(f"  {f.name}  {f.field_type}")

# 堂食侧每个费用字段的 SUM (v10 已 excludes)
print("\n=== 堂食 2 月费用字段合计 (statistics_product, 已 dine_excludes) ===")
unions = []
for u in STORE_UUIDS:
    excl = f"AND sp.sale_order_uuid NOT IN ({','.join(map(str,dine_excl))})" if dine_excl else ""
    unions.append(f"""
SELECT
  IF(sp.free_num>0 OR sp.give_num>0, 0, sp.tax_fee * (sp.product_num - sp.refund_num)) AS tax_fee_kept,
  IF(sp.free_num>0 OR sp.give_num>0, 0, sp.service_fee * (sp.product_num - sp.refund_num)) AS service_fee_kept,
  IF(sp.free_num>0 OR sp.give_num>0, 0, sp.service_tax * (sp.product_num - sp.refund_num)) AS service_tax_kept,
  IF(sp.free_num>0 OR sp.give_num>0, 0, sp.product_final_price * (sp.product_num - sp.refund_num)) AS actual_amount,
  IF(sp.free_num>0 OR sp.give_num>0, 0,
     (sp.product_final_price - sp.tax_fee - sp.service_tax) * (sp.product_num - sp.refund_num)) AS business_amount
FROM `{PROJECT_ID}.shop{u}.ttpos_statistics_product` sp
WHERE sp.complete_time >= {S} AND sp.complete_time < {E} {excl}
""")
sql = f"""WITH a AS ({' UNION ALL '.join(unions)})
SELECT
  ROUND(SUM(tax_fee_kept), 2) AS tax_fee,
  ROUND(SUM(service_fee_kept), 2) AS service_fee,
  ROUND(SUM(service_tax_kept), 2) AS service_tax,
  ROUND(SUM(actual_amount), 2) AS actual_amount,
  ROUND(SUM(business_amount), 2) AS business_amount
FROM a"""
for r in c.query(sql).result():
    print(f"  tax_fee (税费)              : {r['tax_fee']:>14,.2f}")
    print(f"  service_fee (服务费)        : {r['service_fee']:>14,.2f}")
    print(f"  service_tax (服务费税)      : {r['service_tax']:>14,.2f}")
    print(f"  actual_amount (含税)        : {r['actual_amount']:>14,.2f}  ← 我们 v10 口径")
    print(f"  business_amount (不含税)    : {r['business_amount']:>14,.2f}  ← ttpos 另一口径")
    print(f"  差 = tax_fee + service_tax  : {(r['tax_fee'] or 0) + (r['service_tax'] or 0):>14,.2f}")

# 外卖 takeout 表里 schema 已经看过, delivery_fee/small_order_fee/platform_discount等
# 之前已查: delivery_fee=93,275 small_order_fee=? tip=?
# 这里再看下有没有 tip 字段
print("\n=== takeout_order schema 里 tip/fee 字段 ===")
t2 = c.get_table(f"{PROJECT_ID}.shop{STORE_UUIDS[0]}.ttpos_takeout_order")
for f in t2.schema:
    n = f.name.lower()
    if any(k in n for k in ['tip','fee','tax','small','large']):
        print(f"  {f.name}  {f.field_type}")
