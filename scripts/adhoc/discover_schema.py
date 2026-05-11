#!/usr/bin/env python3
"""探测 BQ schema，确认套餐结构存储方式。"""
import sys
sys.path.insert(0, "/Users/tao/Desktop/Projects/analysis")

from bq_reports.utils.bq_client import get_bq_client, setup_proxy

setup_proxy()
client = get_bq_client("diyl-407103")

# 用 admin-001 的 dataset 做探测
# 从 config 或之前的日志可知其 uuid，这里硬编码第一个商家的 uuid
dataset = "shop1958987436032000"
project = "diyl-407103"

# 1. 查 ttpos_product_package 的列名
print("=== ttpos_product_package columns ===")
sql = f"""
SELECT column_name, data_type
FROM `{project}`.`{dataset}`.INFORMATION_SCHEMA.COLUMNS
WHERE table_name = 'ttpos_product_package'
ORDER BY ordinal_position
"""
rows = list(client.query(sql).result())
for row in rows:
    print(f"  {row.column_name}: {row.data_type}")

# 找是否有 parent_uuid / parent / combo 相关字段
parent_like = [r.column_name for r in rows if 'parent' in r.column_name.lower() or 'combo' in r.column_name.lower() or 'type' in r.column_name.lower()]
print(f"\n疑似结构字段: {parent_like}")

# 2. 尝试用 parent_uuid 查套餐结构
print("\n=== 尝试 parent_uuid 查套餐结构 ===")
try:
    sql = f"""
    SELECT parent.uuid AS combo_uuid, child.uuid AS child_uuid,
           JSON_EXTRACT_SCALAR(parent.name, '$.zh') AS combo_name,
           JSON_EXTRACT_SCALAR(child.name, '$.zh') AS child_name
    FROM `{project}`.`{dataset}`.`ttpos_product_package` parent
    JOIN `{project}`.`{dataset}`.`ttpos_product_package` child
      ON child.parent_uuid = parent.uuid
    WHERE parent.delete_time = 0 AND child.delete_time = 0
    LIMIT 20
    """
    rows = list(client.query(sql).result())
    print(f"  成功! 返回 {len(rows)} 行")
    for row in rows[:5]:
        print(f"    {row.combo_name} -> {row.child_name}")
except Exception as e:
    print(f"  失败: {e}")

# 3. 尝试从订单查套餐结构（备用方案）
print("\n=== 从订单查套餐结构 ===")
sql = f"""
SELECT DISTINCT
  parent_sop.product_package_uuid AS combo_uuid,
  child_sop.product_package_uuid AS child_uuid
FROM `{project}`.`{dataset}`.`ttpos_sale_order_product` parent_sop
JOIN `{project}`.`{dataset}`.`ttpos_sale_order_product` child_sop
  ON child_sop.package_uuid = parent_sop.uuid
  AND child_sop.delete_time = 0
WHERE parent_sop.product_type = 1
  AND parent_sop.delete_time = 0
"""
rows = list(client.query(sql).result())
print(f"  返回 {len(rows)} 行")
for row in rows[:5]:
    print(f"    combo={row.combo_uuid}, child={row.child_uuid}")

# 4. 查 ttpos_product_bom 的列名
print("\n=== ttpos_product_bom columns ===")
sql = f"""
SELECT column_name, data_type
FROM `{project}`.`{dataset}`.INFORMATION_SCHEMA.COLUMNS
WHERE table_name = 'ttpos_product_bom'
ORDER BY ordinal_position
"""
rows = list(client.query(sql).result())
for row in rows:
    print(f"  {row.column_name}: {row.data_type}")

# 4b. 查 ttpos_product_bom 记录数
print("\n=== ttpos_product_bom stats ===")
sql = f"""
SELECT COUNT(*) AS total,
       COUNT(DISTINCT product_package_uuid) AS distinct_products
FROM `{project}`.`{dataset}`.`ttpos_product_bom`
WHERE delete_time = 0
"""
rows = list(client.query(sql).result())
for row in rows:
    print(f"  总记录: {row.total}, 不同产品: {row.distinct_products}")

# 4c. 查 product_bom_card 列名（如果存在）
print("\n=== ttpos_product_bom_card columns ===")
try:
    sql = f"""
    SELECT column_name, data_type
    FROM `{project}`.`{dataset}`.INFORMATION_SCHEMA.COLUMNS
    WHERE table_name = 'ttpos_product_bom_card'
    ORDER BY ordinal_position
    """
    rows = list(client.query(sql).result())
    for row in rows:
        print(f"  {row.column_name}: {row.data_type}")
except Exception as e:
    print(f"  表不存在或无法访问: {e}")

# 4d. 查 product_package 的 product_type 分布
print("\n=== ttpos_product_package product_type distribution ===")
sql = f"""
SELECT product_type, COUNT(*) AS cnt
FROM `{project}`.`{dataset}`.`ttpos_product_package`
WHERE delete_time = 0
GROUP BY product_type
ORDER BY cnt DESC
"""
rows = list(client.query(sql).result())
for row in rows:
    print(f"  type={row.product_type}: {row.cnt}")

# 4e. 测试套餐结构查询速度
print("\n=== 套餐结构查询速度测试 ===")
import time
start = time.time()
sql = f"""
SELECT DISTINCT
  parent_sop.product_package_uuid AS combo_uuid,
  child_sop.product_package_uuid AS child_uuid
FROM `{project}`.`{dataset}`.`ttpos_sale_order_product` parent_sop
JOIN `{project}`.`{dataset}`.`ttpos_sale_order_product` child_sop
  ON child_sop.package_uuid = parent_sop.uuid
  AND child_sop.delete_time = 0
WHERE parent_sop.product_type = 1
  AND parent_sop.delete_time = 0
"""
rows = list(client.query(sql).result())
elapsed = time.time() - start
print(f"  耗时: {elapsed:.2f}s, 返回 {len(rows)} 行")

# 5. 查 ttpos_sale_order_product_bom 的列名
print("\n=== ttpos_sale_order_product_bom columns ===")
sql = f"""
SELECT column_name, data_type
FROM `{project}`.`{dataset}`.INFORMATION_SCHEMA.COLUMNS
WHERE table_name = 'ttpos_sale_order_product_bom'
ORDER BY ordinal_position
"""
rows = list(client.query(sql).result())
for row in rows:
    print(f"  {row.column_name}: {row.data_type}")
