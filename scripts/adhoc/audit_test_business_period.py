"""ttpos 后台「测试营业时段」过滤 — 看 2026-02 我们 v10 多算的金额是不是 ~13,837。

ttpos 源码: repository/common.go:836 ExcludeTestBusinessByBillSQL
  NOT EXISTS (
    SELECT 1 FROM ttpos_sale_bill _sb
    JOIN ttpos_business_status_period bsp ON bsp.delete_time=0
      AND _sb.create_time >= bsp.start_time
      AND (bsp.end_time = 0 OR _sb.create_time <= bsp.end_time)
    WHERE _sb.uuid = sp.sale_bill_uuid AND _sb.delete_time = 0)
"""
import sys, csv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID, STORE_UUIDS

setup_proxy(); c = get_bq_client()

with open('resources/wallace.20260515/order_excludes.csv', encoding='utf-8-sig') as f:
    dine_excl = set(int(r['exclude_key']) for r in csv.DictReader(f) if r['channel']=='dine')
with open('resources/wallace.20260515/order_excludes.csv', encoding='utf-8-sig') as f:
    take_excl = set(int(r['exclude_key']) for r in csv.DictReader(f) if r['channel']=='takeout')

S = "UNIX_SECONDS(TIMESTAMP('2026-02-01 00:00:00+07'))"
E = "UNIX_SECONDS(TIMESTAMP('2026-03-01 00:00:00+07'))"

# Part 1: 列出 53 店 business_status_period 表内容
print("=== 53 店 ttpos_business_status_period 内容 (delete_time=0) ===")
periods_unions = []
for u in STORE_UUIDS:
    periods_unions.append(f"""
SELECT '{u}' AS store, uuid, start_time, end_time
FROM `{PROJECT_ID}.shop{u}.ttpos_business_status_period`
WHERE delete_time = 0
""")
sql_p = f"WITH a AS ({' UNION ALL '.join(periods_unions)}) SELECT * FROM a ORDER BY store, start_time"
rows = list(c.query(sql_p).result())
print(f"总记录数: {len(rows)}")
from datetime import datetime, timezone, timedelta
bkk = timezone(timedelta(hours=7))
for r in rows[:30]:
    s = datetime.fromtimestamp(r['start_time'], bkk) if r['start_time'] else None
    e = datetime.fromtimestamp(r['end_time'], bkk) if r['end_time'] else "进行中"
    print(f"  店 {r['store']}  start={s}  end={e}")
if len(rows) > 30:
    print(f"  ... 还有 {len(rows)-30} 条")

# Part 2: 2 月份 statistics_product 里, 关联 sale_bill.create_time 落在测试营业期内的金额
print("\n=== 2 月份测试营业时段内的应排除金额 (堂食) ===")
dine_unions = []
for u in STORE_UUIDS:
    excl = f"AND sp.sale_order_uuid NOT IN ({','.join(map(str,dine_excl))})" if dine_excl else ""
    dine_unions.append(f"""
SELECT
  '{u}' AS store,
  IF(sp.free_num>0 OR sp.give_num>0, 0,
     sp.product_final_price*(sp.product_num - sp.refund_num)) AS actual_amount,
  EXISTS(
    SELECT 1 FROM `{PROJECT_ID}.shop{u}.ttpos_business_status_period` bsp
    WHERE bsp.delete_time = 0
      AND sb.create_time >= bsp.start_time
      AND (bsp.end_time = 0 OR sb.create_time <= bsp.end_time)
  ) AS in_test_period
FROM `{PROJECT_ID}.shop{u}.ttpos_statistics_product` sp
JOIN `{PROJECT_ID}.shop{u}.ttpos_sale_bill` sb ON sb.uuid = sp.sale_bill_uuid AND sb.delete_time = 0
WHERE sp.complete_time >= {S} AND sp.complete_time < {E}
  {excl}
""")
sql_d = f"""
WITH r AS ({' UNION ALL '.join(dine_unions)})
SELECT store,
  COUNT(*) FILTER (WHERE in_test_period) AS in_test_rows,
  ROUND(SUM(IF(in_test_period, actual_amount, 0)),2) AS in_test_actual,
  ROUND(SUM(actual_amount),2) AS total_actual
FROM r GROUP BY store
HAVING in_test_rows > 0
ORDER BY in_test_actual DESC
"""
# 注意: BQ 不支持 COUNT(*) FILTER(WHERE...), 改用 COUNTIF
sql_d = f"""
WITH r AS ({' UNION ALL '.join(dine_unions)})
SELECT store,
  COUNTIF(in_test_period) AS in_test_rows,
  ROUND(SUM(IF(in_test_period, actual_amount, 0)),2) AS in_test_actual,
  ROUND(SUM(actual_amount),2) AS total_actual
FROM r GROUP BY store
HAVING in_test_rows > 0
ORDER BY in_test_actual DESC
"""
total_drop = 0
for r in c.query(sql_d).result():
    print(f"  店 {r['store']}  测试期行数={r['in_test_rows']}  应排除实收={r['in_test_actual']:,.2f}  当店总实收={r['total_actual']:,.2f}")
    total_drop += r['in_test_actual']
print(f"  >>> 堂食应排除合计: {total_drop:,.2f}")

# 外卖侧也对一下 — 但 takeout_order 没有 sale_bill_uuid, ttpos 怎么过滤的?
# 外卖统计走 takeout_order.create_time 直接对 business_status_period
print("\n=== 2 月份外卖测试营业时段内的应排除金额 ===")
tk_unions = []
for u in STORE_UUIDS:
    excl = f"AND t.uuid NOT IN ({','.join(map(str,take_excl))})" if take_excl else ""
    tk_unions.append(f"""
SELECT
  '{u}' AS store,
  IF(t.order_state IN (10,20,30,40), toi.price * toi.quantity, 0) AS actual_amount,
  EXISTS(
    SELECT 1 FROM `{PROJECT_ID}.shop{u}.ttpos_business_status_period` bsp
    WHERE bsp.delete_time = 0
      AND t.create_time >= bsp.start_time
      AND (bsp.end_time = 0 OR t.create_time <= bsp.end_time)
  ) AS in_test_period
FROM `{PROJECT_ID}.shop{u}.ttpos_takeout_order_item` toi
JOIN `{PROJECT_ID}.shop{u}.ttpos_takeout_order` t ON t.uuid=toi.takeout_order_uuid AND t.delete_time=0
WHERE toi.delete_time=0 AND toi.ttpos_product_package_uuid>0
  AND t.order_state IN (10,20,30,40,60) AND t.accepted_time>0
  AND ((t.order_state=40 AND t.completed_time>={S} AND t.completed_time<{E})
       OR (t.order_state!=40 AND t.accepted_time>={S} AND t.accepted_time<{E}))
  {excl}
""")
sql_t = f"""
WITH r AS ({' UNION ALL '.join(tk_unions)})
SELECT store,
  COUNTIF(in_test_period) AS rows,
  ROUND(SUM(IF(in_test_period, actual_amount, 0)),2) AS in_test_actual
FROM r GROUP BY store HAVING rows > 0 ORDER BY in_test_actual DESC
"""
total_tk = 0
for r in c.query(sql_t).result():
    print(f"  店 {r['store']}  测试期行数={r['rows']}  应排除实收={r['in_test_actual']:,.2f}")
    total_tk += r['in_test_actual']
print(f"  >>> 外卖应排除合计: {total_tk:,.2f}")

print(f"\n*** 总应排除 (堂食+外卖) = {total_drop+total_tk:,.2f} ***")
print(f"*** 跟客户差额 ฿13,837 对比 ***")
