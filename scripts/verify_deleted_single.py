#!/usr/bin/env python3
"""验证：已删除单品的BOM配置情况（精确版）"""
import sys
sys.path.insert(0, "/home/weifashi/hwt/analysis")

from bq_reports.utils.bq_client import get_bq_client, setup_proxy
from datetime import datetime, timezone

setup_proxy()
client = get_bq_client()
project = "diyl-407103"
dataset = "shop1958987436032000"

START_TS = 1756684800
END_TS = 1775001600

# 1. 已删除单品 - 精确判断有无BOM
print("=" * 60)
print("已删除单品的BOM配置情况（精确版）")
sql = f"""
WITH deleted_single AS (
  SELECT
    uuid,
    delete_time,
    REGEXP_REPLACE(COALESCE(
      JSON_EXTRACT_SCALAR(name, '$.zh'),
      JSON_EXTRACT_SCALAR(name, '$.en'),
      ''
    ), r'^\\s+|\\s+$', '') AS product_name
  FROM `{project}`.`{dataset}`.`ttpos_product_package`
  WHERE product_type = 0 AND delete_time > 0
),
bom_exists AS (
  SELECT DISTINCT pb.product_package_uuid
  FROM `{project}`.`{dataset}`.`ttpos_product_bom` pb
  WHERE pb.product_bom_card_uuid > 0
    AND pb.product_package_uuid IN (SELECT uuid FROM deleted_single)
),
material_exists AS (
  SELECT DISTINCT pb.product_package_uuid
  FROM `{project}`.`{dataset}`.`ttpos_product_bom` pb
  JOIN `{project}`.`{dataset}`.`ttpos_related_material` rm
    ON rm.related_uuid = pb.product_bom_card_uuid
    AND rm.delete_time = 0
  WHERE pb.product_bom_card_uuid > 0
    AND pb.product_package_uuid IN (SELECT uuid FROM deleted_single)
)
SELECT
  ds.uuid,
  ds.delete_time,
  ds.product_name,
  CASE WHEN be.product_package_uuid IS NOT NULL THEN 1 ELSE 0 END AS has_bom_card,
  CASE WHEN me.product_package_uuid IS NOT NULL THEN 1 ELSE 0 END AS has_material
FROM deleted_single ds
LEFT JOIN bom_exists be ON be.product_package_uuid = ds.uuid
LEFT JOIN material_exists me ON me.product_package_uuid = ds.uuid
ORDER BY ds.delete_time DESC
"""
rows = list(client.query(sql).result())

total = len(rows)
with_bom_card = sum(1 for r in rows if r.has_bom_card)
with_material = sum(1 for r in rows if r.has_material)
no_bom = sum(1 for r in rows if not r.has_bom_card)

print(f"  已删除单品总数: {total}")
print(f"  有BOM卡记录的: {with_bom_card}")
print(f"  有实际物料的: {with_material}")
print(f"  无BOM配置的: {no_bom}")

# 按删除时间范围
in_range = [r for r in rows if START_TS <= r.delete_time < END_TS]
print(f"\n  2025-09 至 2026-03 期间删除的: {len(in_range)} 个")
if in_range:
    in_with = sum(1 for r in in_range if r.has_material)
    in_no = sum(1 for r in in_range if not r.has_bom_card)
    print(f"    其中有物料的: {in_with}")
    print(f"    无BOM的: {in_no}")

# 有BOM但查不到物料的（BOM卡存在但物料缺失）
bom_no_material = [r for r in rows if r.has_bom_card and not r.has_material]
if bom_no_material:
    print(f"\n  ⚠️ 有BOM卡但物料查不到的: {len(bom_no_material)} 个")
    for r in bom_no_material[:5]:
        dt = datetime.fromtimestamp(r.delete_time, tz=timezone.utc).strftime('%Y-%m-%d')
        print(f"    - {r.product_name} (删除于 {dt})")

# 无BOM的
no_bom_list = [r for r in rows if not r.has_bom_card]
if no_bom_list:
    print(f"\n  无BOM配置的单品 ({len(no_bom_list)} 个):")
    for r in no_bom_list[:10]:
        dt = datetime.fromtimestamp(r.delete_time, tz=timezone.utc).strftime('%Y-%m-%d')
        print(f"    - {r.product_name} (删除于 {dt})")
    if len(no_bom_list) > 10:
        print(f"    ... 还有 {len(no_bom_list) - 10} 个")

print("\n验证完成")
