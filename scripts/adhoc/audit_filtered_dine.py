"""Audit: 堂食侧 — 当前 ttpos_statistics_product SQL 完全没做 hide/delete 过滤,
看看 2026-02 窗口里有多少行的 sale_bill 实际上是被隐藏 / 软删的, 但报表还是算了。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID, STORE_UUIDS

setup_proxy()
client = get_bq_client()

START_TS = "UNIX_SECONDS(TIMESTAMP('2026-02-01 00:00:00+07'))"
END_TS = "UNIX_SECONDS(TIMESTAMP('2026-03-01 00:00:00+07'))"

datasets = [f"shop{u}" for u in STORE_UUIDS]
print(f"扫描 {len(datasets)} 个 shop dataset (堂食)")

unions = []
for ds in datasets:
    unions.append(f"""
SELECT
  '{ds}' AS dataset,
  sp.sale_bill_uuid,
  sp.sale_order_uuid,
  sp.product_sale_price * sp.product_num AS gmv,
  sp.delete_time AS sp_delete_time,
  sb.hide_bill_time,
  sb.delete_time AS sb_delete_time,
  sb.status AS sb_status
FROM `{PROJECT_ID}.{ds}.ttpos_statistics_product` sp
LEFT JOIN `{PROJECT_ID}.{ds}.ttpos_sale_bill` sb
  ON sb.uuid = sp.sale_bill_uuid
WHERE sp.complete_time >= {START_TS} AND sp.complete_time < {END_TS}
""")

sql = f"""
WITH all_rows AS ({' UNION ALL '.join(unions)}),
classified AS (
  SELECT
    CASE
      WHEN sp_delete_time != 0 THEN '1_statistics_product_soft_deleted'
      WHEN sale_bill_uuid IS NULL OR sale_bill_uuid = 0 THEN '2_no_sale_bill_link'
      WHEN sb_delete_time != 0 THEN '3_sale_bill_soft_deleted'
      WHEN hide_bill_time != 0 THEN '4_sale_bill_HIDDEN (hide_bill_time!=0)'
      ELSE '5_kept_normal'
    END AS reason,
    gmv
  FROM all_rows
)
SELECT reason, COUNT(*) AS row_count, ROUND(SUM(gmv),2) AS gmv_sum
FROM classified GROUP BY reason ORDER BY reason
"""

print("查询中...")
rows = list(client.query(sql).result())
print()
print(f"{'reason':<50} {'row_count':>12} {'gmv_sum':>15}")
print("-" * 80)
for r in rows:
    print(f"{r['reason']:<50} {r['row_count']:>12,} {r['gmv_sum'] or 0:>15,.2f}")
