"""对账: csv 里的 201 条 dine 排除 vs BQ 上 2 月份 ttpos_sale_bill.hide_bill_time != 0 的真实隐藏单
看 csv 是否完整覆盖了客服在 ttpos 后台操作过的所有 2 月隐藏单。"""
import sys, csv as csvm
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID, STORE_UUIDS

setup_proxy(); c = get_bq_client()

with open('resources/wallace.20260515/order_excludes.csv', encoding='utf-8-sig') as f:
    csv_rows = list(csvm.DictReader(f))
csv_dine = set(int(r['exclude_key']) for r in csv_rows if r['channel']=='dine')
print(f"csv dine 排除条数: {len(csv_dine)}")

S = "UNIX_SECONDS(TIMESTAMP('2026-02-01 00:00:00+07'))"
E = "UNIX_SECONDS(TIMESTAMP('2026-03-01 00:00:00+07'))"

# 关键: 客服操作的目标是 sale_bill (整张账单), csv 写的是 sale_order_uuid (单)
# 我们要查: 2 月份 complete_time 窗口内的 statistics_product, 其 sale_bill.hide_bill_time != 0 的 sale_order_uuid
unions = []
for u in STORE_UUIDS:
    unions.append(f"""
SELECT DISTINCT
  '{u}' AS store,
  sp.sale_order_uuid,
  sp.sale_bill_uuid,
  sb.hide_bill_time,
  IF(sp.free_num>0 OR sp.give_num>0, 0,
     sp.product_final_price*(sp.product_num - sp.refund_num)) AS line_actual
FROM `{PROJECT_ID}.shop{u}.ttpos_statistics_product` sp
JOIN `{PROJECT_ID}.shop{u}.ttpos_sale_bill` sb ON sb.uuid = sp.sale_bill_uuid
WHERE sp.complete_time >= {S} AND sp.complete_time < {E}
  AND sb.hide_bill_time != 0
""")

sql = f"""
WITH hidden_in_stats AS ({' UNION ALL '.join(unions)})
SELECT store, sale_order_uuid, sale_bill_uuid, hide_bill_time, SUM(line_actual) AS actual
FROM hidden_in_stats
GROUP BY store, sale_order_uuid, sale_bill_uuid, hide_bill_time
"""

ttpos_hidden = list(c.query(sql).result())
print(f"BQ 上 2 月 complete_time 窗口内, hide_bill_time!=0 且在 statistics_product 里有对应行的 sale_order: {len(ttpos_hidden)}")

# 比较
ttpos_set = set(int(r['sale_order_uuid']) for r in ttpos_hidden)
in_csv = ttpos_set & csv_dine
not_in_csv = ttpos_set - csv_dine
csv_not_in_ttpos = csv_dine - ttpos_set

print(f"  ✅ 两边都有: {len(in_csv)}")
print(f"  ⚠️  在 BQ 隐藏但 csv 没有 (漏过滤): {len(not_in_csv)}")
print(f"  ℹ️  在 csv 但 BQ 不算隐藏: {len(csv_not_in_ttpos)}")

if not_in_csv:
    print("\n=== 漏过滤的 sale_order (BQ 标记 hide_bill_time!=0 但 csv 没列): ===")
    for r in ttpos_hidden:
        if int(r['sale_order_uuid']) in not_in_csv:
            print(f"  店 {r['store']}  sale_order_uuid={r['sale_order_uuid']}  bill={r['sale_bill_uuid']}  实收={r['actual']:.2f}")
    miss_amount = sum(r['actual'] for r in ttpos_hidden if int(r['sale_order_uuid']) in not_in_csv)
    print(f"  漏过滤金额合计: {miss_amount:,.2f}")
