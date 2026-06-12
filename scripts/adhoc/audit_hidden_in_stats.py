"""3214 个 hide_bill_time != 0 的隐藏单, 有多少在 ttpos_statistics_product 里出现?
   还要看其它金额字段, 估算被"隐藏"掉的销量。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID, STORE_UUIDS

setup_proxy()
c = get_bq_client()

unions = []
for u in STORE_UUIDS:
    unions.append(f"""
SELECT
  '{u}' AS store,
  sb.uuid AS bill_uuid,
  sb.hide_bill_time,
  sb.create_time AS bill_create_time,
  sb.product_amount AS bill_amount,
  sb.status AS bill_status,
  (SELECT COUNT(1) FROM `{PROJECT_ID}.shop{u}.ttpos_statistics_product` sp
    WHERE sp.sale_bill_uuid = sb.uuid) AS stats_rows,
  (SELECT SUM(sp.product_sale_price * sp.product_num)
   FROM `{PROJECT_ID}.shop{u}.ttpos_statistics_product` sp
    WHERE sp.sale_bill_uuid = sb.uuid) AS stats_gmv
FROM `{PROJECT_ID}.shop{u}.ttpos_sale_bill` sb
WHERE sb.hide_bill_time != 0
""")

sql = f"""
WITH hidden AS ({' UNION ALL '.join(unions)})
SELECT
  COUNT(*) AS hidden_bills,
  SUM(IF(stats_rows > 0, 1, 0)) AS hidden_with_stats_rows,
  SUM(stats_rows)             AS total_stats_rows_in_hidden,
  ROUND(SUM(stats_gmv), 2)    AS total_stats_gmv_in_hidden,
  ROUND(SUM(bill_amount), 2)  AS total_bill_amount_in_hidden,
  -- 按 bill_status 分组也看下
  COUNTIF(bill_status = 1) AS status_1,
  COUNTIF(bill_status = 2) AS status_2,
  COUNTIF(bill_status = 3) AS status_3,
  COUNTIF(bill_status = 4) AS status_4,
  COUNTIF(bill_status NOT IN (1,2,3,4)) AS status_other
FROM hidden
"""
for r in c.query(sql).result():
    print(f"hidden bills (sale_bill.hide_bill_time != 0):                   {r['hidden_bills']:,}")
    print(f"  其中 statistics_product 有对应行的:                            {r['hidden_with_stats_rows']:,}")
    print(f"  这些隐藏单在 statistics_product 里的总行数:                    {r['total_stats_rows_in_hidden'] or 0:,}")
    print(f"  这些隐藏单在 statistics_product 里的 GMV:                      {r['total_stats_gmv_in_hidden'] or 0:,.2f}")
    print(f"  这些隐藏单 sale_bill.product_amount 之和:                      {r['total_bill_amount_in_hidden'] or 0:,.2f}")
    print(f"  bill_status 分布: 1={r['status_1']}  2={r['status_2']}  3={r['status_3']}  4={r['status_4']}  其它={r['status_other']}")
