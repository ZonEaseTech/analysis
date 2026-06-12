"""Audit: 当前 SQL 在 2026-03 窗口里实际会过滤掉哪些外卖订单 (按原因分桶)。

只统计外卖 (takeout) — 堂食 (statistics_product) 走预聚合表, 报表层只有时间窗过滤,
源头是否包含 hide_bill_time / 异常订单由 ttpos 后台决定, 这里无法旁观。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID, STORE_UUIDS

setup_proxy()
client = get_bq_client()

# 2026-03 BKK 时间窗
START_TS = "UNIX_SECONDS(TIMESTAMP('2026-02-01 00:00:00+07'))"
END_TS = "UNIX_SECONDS(TIMESTAMP('2026-03-01 00:00:00+07'))"

datasets = [f"shop{u}" for u in STORE_UUIDS]
print(f"扫描 {len(datasets)} 个 shop dataset")

# UNION ALL 一次跑完
unions = []
for ds in datasets:
    unions.append(f"""
SELECT
  '{ds}' AS dataset,
  t.uuid AS order_uuid,
  t.delete_time,
  t.accepted_time,
  t.order_state,
  t.subtotal
FROM `{PROJECT_ID}.{ds}.ttpos_takeout_order` t
WHERE
  -- 落在 3 月窗口里的所有订单 (不管会不会被过滤)
  (
    (t.order_state = 40 AND t.completed_time >= {START_TS} AND t.completed_time < {END_TS})
    OR (t.accepted_time >= {START_TS} AND t.accepted_time < {END_TS})
  )
""")
sql = f"""
WITH all_orders AS ({' UNION ALL '.join(unions)}),
classified AS (
  SELECT
    CASE
      WHEN delete_time != 0 THEN '1_order_soft_deleted (delete_time!=0)'
      WHEN accepted_time = 0 THEN '2_no_accepted_time'
      WHEN order_state NOT IN (10,20,30,40,60)
        THEN CONCAT('3_state_excluded_', CAST(order_state AS STRING))
      WHEN order_state = 60 THEN '4_kept_but_revenue_zero (state=60 取消)'
      ELSE '5_kept'
    END AS reason,
    order_uuid,
    subtotal
  FROM all_orders
)
SELECT
  reason,
  COUNT(*) AS order_count,
  ROUND(SUM(subtotal), 2) AS subtotal_sum
FROM classified
GROUP BY reason
ORDER BY reason
"""

print("查询中...")
rows = list(client.query(sql).result())
print()
print(f"{'reason':<50} {'order_count':>12} {'subtotal':>15}")
print("-" * 80)
for r in rows:
    print(f"{r['reason']:<50} {r['order_count']:>12,} {r['subtotal_sum'] or 0:>15,.2f}")
