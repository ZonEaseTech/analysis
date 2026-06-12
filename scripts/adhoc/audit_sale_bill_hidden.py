"""Audit: BQ 上 ttpos_sale_bill 总共有多少 hide_bill_time != 0 / delete_time != 0 的行 (跨全部 53 店, 全时间窗)。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID, STORE_UUIDS

setup_proxy()
c = get_bq_client()

unions = [
    f"SELECT '{u}' AS store, hide_bill_time, delete_time, create_time "
    f"FROM `{PROJECT_ID}.shop{u}.ttpos_sale_bill`"
    for u in STORE_UUIDS
]
sql = f"""
WITH a AS ({' UNION ALL '.join(unions)})
SELECT
  COUNT(*) AS total_rows,
  SUM(IF(hide_bill_time != 0, 1, 0)) AS hidden_rows,
  SUM(IF(delete_time != 0, 1, 0))    AS soft_deleted_rows,
  SUM(IF(hide_bill_time != 0 AND delete_time = 0, 1, 0)) AS hidden_only,
  MIN(IF(hide_bill_time != 0, create_time, NULL)) AS earliest_hidden_create,
  MAX(IF(hide_bill_time != 0, create_time, NULL)) AS latest_hidden_create
FROM a
"""
for r in c.query(sql).result():
    print(f"全 53 店 ttpos_sale_bill 总行数: {r['total_rows']:,}")
    print(f"  hide_bill_time != 0 (POS 隐藏): {r['hidden_rows']:,}")
    print(f"  delete_time     != 0 (软删):   {r['soft_deleted_rows']:,}")
    print(f"  仅隐藏未软删:                   {r['hidden_only']:,}")
    if r['earliest_hidden_create']:
        from datetime import datetime, timezone, timedelta
        bkk = timezone(timedelta(hours=7))
        e = datetime.fromtimestamp(r['earliest_hidden_create'], bkk)
        l = datetime.fromtimestamp(r['latest_hidden_create'], bkk)
        print(f"  隐藏单 create_time 跨度: {e} → {l}")
