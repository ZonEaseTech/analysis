# ⚠️ 一次性排查脚本 (2026-02 profit_by_price 可疑标记扫描)。
# 硬编码了 2026-02 月份 + STORE_UUIDS + order_excludes.csv。
# 通用离群检测见 semantic/analytics/variance_decomposition.py。
# 本脚本保留作该月特定数据缺口排查的审计证据。
#
"""扫描 v10 (2026-02 profit_by_price) 已计入的行里, 哪些带'可疑'标记 —— 用来反向定位
跟客户那边 2733 万实收的差额来源 (我们 v10 实收 27,343,837, 差 ~13,837)。"""
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

dine_unions = []
for u in STORE_UUIDS:
    excl = f"AND sp.sale_order_uuid NOT IN ({','.join(map(str,dine_excl))})" if dine_excl else ""
    dine_unions.append(f"""
SELECT
  IF(sp.free_num>0 OR sp.give_num>0, 0,
     sp.product_final_price*(sp.product_num - sp.refund_num)) AS actual_amount,
  sb.hide_bill_time, sb.delete_time AS sb_delete_time, so.delete_time AS so_delete_time,
  sp.delete_time AS sp_delete_time,
  sb.status AS sb_status, so.status AS so_status,
  sp.refund_time
FROM `{PROJECT_ID}.shop{u}.ttpos_statistics_product` sp
LEFT JOIN `{PROJECT_ID}.shop{u}.ttpos_sale_bill` sb ON sb.uuid = sp.sale_bill_uuid
LEFT JOIN `{PROJECT_ID}.shop{u}.ttpos_sale_order` so ON so.uuid = sp.sale_order_uuid
WHERE sp.complete_time >= {S} AND sp.complete_time < {E} {excl}
""")

sql = f"""
WITH r AS ({' UNION ALL '.join(dine_unions)})
SELECT
  CASE
    WHEN hide_bill_time != 0 THEN 'A_sale_bill.hide_bill_time'
    WHEN sb_delete_time != 0 THEN 'B_sale_bill.delete_time'
    WHEN so_delete_time != 0 THEN 'C_sale_order.delete_time'
    WHEN sp_delete_time != 0 THEN 'D_stats_product.delete_time'
    WHEN refund_time != 0     THEN 'E_stats_product.refund_time'
    ELSE 'Z_clean'
  END AS flag,
  COUNT(*) AS n,
  ROUND(SUM(actual_amount),2) AS amount
FROM r GROUP BY flag ORDER BY flag
"""
print("=== 堂食: v10 里仍包含的行 按可疑标记分桶 ===")
total_susp = 0
for row in c.query(sql).result():
    print(f"  {row['flag']:<35} {row['n']:>8}  {row['amount'] or 0:>14,.2f}")
    if row['flag'] != 'Z_clean':
        total_susp += (row['amount'] or 0)
print(f"  可疑合计: {total_susp:,.2f}")

sql_status = f"""
WITH r AS ({' UNION ALL '.join(dine_unions)})
SELECT sb_status, COUNT(*) AS n, ROUND(SUM(actual_amount),2) AS amount
FROM r GROUP BY sb_status ORDER BY sb_status
"""
print()
print("=== 堂食: 按 sale_bill.status 分布 ===")
for row in c.query(sql_status).result():
    print(f"  sb_status={str(row['sb_status']):<6}  {row['n']:>8}  {row['amount'] or 0:>14,.2f}")

tk_unions = []
for u in STORE_UUIDS:
    excl = f"AND t.uuid NOT IN ({','.join(map(str,take_excl))})" if take_excl else ""
    tk_unions.append(f"""
SELECT t.order_state,
  IF(t.order_state IN (10,20,30,40), toi.price*toi.quantity, 0) AS actual_amount
FROM `{PROJECT_ID}.shop{u}.ttpos_takeout_order_item` toi
JOIN `{PROJECT_ID}.shop{u}.ttpos_takeout_order` t ON t.uuid=toi.takeout_order_uuid AND t.delete_time=0
WHERE toi.delete_time=0 AND toi.ttpos_product_package_uuid>0
  AND t.order_state IN (10,20,30,40,60) AND t.accepted_time>0
  AND ((t.order_state=40 AND t.completed_time>={S} AND t.completed_time<{E})
       OR (t.order_state!=40 AND t.accepted_time>={S} AND t.accepted_time<{E}))
  {excl}
""")
sql_tk = f"WITH r AS ({' UNION ALL '.join(tk_unions)}) SELECT order_state, COUNT(*) n, ROUND(SUM(actual_amount),2) amount FROM r GROUP BY order_state ORDER BY order_state"
print()
print("=== 外卖: 按 order_state 分布 (v10 已扣 14 单 excludes) ===")
for row in c.query(sql_tk).result():
    print(f"  state={str(row['order_state']):<5}  {row['n']:>8}  {row['amount'] or 0:>14,.2f}")
