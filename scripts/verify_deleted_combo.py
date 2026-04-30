#!/usr/bin/env python3
"""验证：已删除套餐数据完整性"""
import sys
sys.path.insert(0, "/home/weifashi/hwt/analysis")

from bq_reports.utils.bq_client import get_bq_client, setup_proxy
from datetime import datetime, timezone

setup_proxy()
client = get_bq_client()
project = "diyl-407103"
dataset = "shop1958987436032000"

# 正确的时间戳
START_TS = 1756684800   # 2025-09-01 00:00:00 UTC
END_TS = 1775001600     # 2026-04-01 00:00:00 UTC

def fmt_ts(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')

# 1. 所有已删除的套餐
print("=" * 60)
print("Step 1: 已删除的套餐 (product_type = 1, delete_time > 0)")
sql1 = f"""
SELECT
  uuid,
  delete_time,
  REGEXP_REPLACE(COALESCE(
    JSON_EXTRACT_SCALAR(name, '$.zh'),
    JSON_EXTRACT_SCALAR(name, '$.en'),
    ''
  ), r'^\\s+|\\s+$', '') AS combo_name,
  price
FROM `{project}`.`{dataset}`.`ttpos_product_package`
WHERE product_type = 1 AND delete_time > 0
ORDER BY delete_time DESC
"""
rows1 = list(client.query(sql1).result())
print(f"  已删除套餐总数: {len(rows1)}")

year_month = {}
for r in rows1:
    dt = datetime.fromtimestamp(r.delete_time, tz=timezone.utc)
    ym = dt.strftime('%Y-%m')
    year_month[ym] = year_month.get(ym, 0) + 1

print("  按删除年月分布:")
for ym, cnt in sorted(year_month.items()):
    print(f"    {ym}: {cnt} 个")

# 客户关注的时间段内被删除的
del_in_range = [r for r in rows1 if START_TS <= r.delete_time < END_TS]
print(f"\n  2025-09 至 2026-03 期间删除的: {len(del_in_range)} 个")

# 2. 从销售记录找"在 2025-09 至 2026-03 期间销售过"的已删除套餐
print("\n" + "=" * 60)
print("Step 2: 销售记录验证 (2025-09 至 2026-03)")

# 堂食 statistics_product
sql2a = f"""
SELECT DISTINCT sp.product_package_uuid AS combo_uuid
FROM `{project}`.`{dataset}`.`ttpos_statistics_product` sp
WHERE sp.complete_time >= {START_TS}
  AND sp.complete_time < {END_TS}
  AND sp.delete_time = 0
  AND sp.product_package_uuid IN (
    SELECT uuid FROM `{project}`.`{dataset}`.`ttpos_product_package`
    WHERE product_type = 1 AND delete_time > 0
  )
"""
rows2a = list(client.query(sql2a).result())
print(f"  堂食销售过的已删除套餐: {len(rows2a)} 个")

# 外卖 takeout_order_item
sql2b = f"""
SELECT DISTINCT toi.ttpos_product_package_uuid AS combo_uuid
FROM `{project}`.`{dataset}`.`ttpos_takeout_order_item` toi
JOIN `{project}`.`{dataset}`.`ttpos_takeout_order` tko
  ON tko.uuid = toi.takeout_order_uuid AND tko.delete_time = 0
WHERE toi.delete_time = 0
  AND tko.order_state IN (10, 20, 30, 40, 60)
  AND toi.ttpos_product_package_uuid IN (
    SELECT uuid FROM `{project}`.`{dataset}`.`ttpos_product_package`
    WHERE product_type = 1 AND delete_time > 0
  )
  AND ((tko.completed_time > 0 AND tko.completed_time >= {START_TS} AND tko.completed_time < {END_TS})
    OR (tko.accepted_time > 0 AND tko.accepted_time >= {START_TS} AND tko.accepted_time < {END_TS}))
"""
rows2b = list(client.query(sql2b).result())
print(f"  外卖销售过的已删除套餐: {len(rows2b)} 个")

sold_deleted = set(r.combo_uuid for r in rows2a) | set(r.combo_uuid for r in rows2b)
print(f"  合并去重后（该门店）: {len(sold_deleted)} 个")

if sold_deleted:
    uuid_to_name = {r.uuid: r.combo_name for r in rows1}
    print("\n  这些套餐是:")
    for uid in sorted(sold_deleted, key=lambda u: uuid_to_name.get(u, "")):
        del_time = next((r.delete_time for r in rows1 if r.uuid == uid), 0)
        print(f"    - {uuid_to_name.get(uid, uid)} (删除于 {fmt_ts(del_time)})")

# 3. 分组记录完整性检查（针对所有已删除套餐）
print("\n" + "=" * 60)
print("Step 3: 已删除套餐的分组记录完整性")
if rows1:
    del_uuids_str = ",".join(str(r.uuid) for r in rows1)

    sql3 = f"""
    SELECT
      pgrp.product_package_uuid,
      COUNT(*) AS grp_cnt,
      SUM(CASE WHEN pgrp.delete_time = 0 THEN 1 ELSE 0 END) AS active_grp,
      SUM(CASE WHEN pgrp.delete_time > 0 THEN 1 ELSE 0 END) AS deleted_grp
    FROM `{project}`.`{dataset}`.`ttpos_product_package_group` pgrp
    WHERE pgrp.product_package_uuid IN ({del_uuids_str})
    GROUP BY pgrp.product_package_uuid
    """
    rows3 = list(client.query(sql3).result())
    print(f"  有分组记录的套餐数: {len(rows3)} / {len(rows1)}")

    has_group = {r.product_package_uuid for r in rows3}
    no_group = [r for r in rows1 if r.uuid not in has_group]
    if no_group:
        print(f"  ⚠️ 无分组记录: {len(no_group)} 个")
        for r in no_group[:5]:
            print(f"    - {r.combo_name}")

    sql4 = f"""
    WITH groups AS (
      SELECT uuid AS group_uuid, product_package_uuid
      FROM `{project}`.`{dataset}`.`ttpos_product_package_group`
      WHERE product_package_uuid IN ({del_uuids_str})
    )
    SELECT
      g.product_package_uuid,
      COUNT(*) AS item_cnt
    FROM groups g
    JOIN `{project}`.`{dataset}`.`ttpos_product_package_group_item` gpi
      ON gpi.product_package_group_uuid = g.group_uuid
    GROUP BY g.product_package_uuid
    """
    rows4 = list(client.query(sql4).result())
    print(f"  有子项记录的套餐数: {len(rows4)} / {len(rows1)}")

print("\n" + "=" * 60)
print("验证完成")
