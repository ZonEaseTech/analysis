"""Combo (套餐) → child product mapping.

**真源**: ttpos_product_package_group + ttpos_product_package_group_item
（套餐分组定义 + 分组内的子商品配置）。

跟旧版（从堂食订单 sale_order_product 反推）的区别:
  ✅ 不依赖订单数据 → 已删 / 只外卖卖 / 还没卖过的套餐都查得到
  ✅ 返回 (child_uuid, num, weight) 三元组
       num: 该子商品在分组里的份数
       weight: 按 "optional_count / candidate_count" 比例摊薄
               (例: optional_count=3 candidate_count=6 → weight=0.5 表示客户期望选半数)
  ✅ 软删 fallback (跟 BOM_SQL 同套路): 全删时回退到 deleted 行
  ⚠️  不再吃 {start_ts}/{end_ts} 参数（套餐定义跨月稳定）

数据结构示例（超值套餐 7 实测，optional_count=3, candidate_count=6）:
  combo "超值套餐 7"
    └─ group "小食" optional_count=3
        ├─ slot 1: child=A, num=4   weight=0.5
        ├─ slot 2: child=B, num=2   weight=0.5
        ├─ slot 3: child=C, num=2   weight=0.5
        ├─ slot 4: child=D, num=2   weight=0.5
        ├─ slot 5: child=C, num=2   weight=0.5  (同 child 多 slot 算多次)
        └─ slot 6: child=A, num=4   weight=0.5
  期望成本 = sum(slot.num × child_bom) × 0.5
"""


def combo_structure_sql() -> str:
    """Returns the SQL. 输出列: combo_uuid / child_uuid / child_num / weight。

    Caller 端把这 4 列收集成 {combo_uuid: [(child_uuid, num, weight), ...]}。
    """
    return """
WITH group_with_flag AS (
  -- 软删 fallback: 跟 BOM_SQL 同套路。一个 combo 没 active group 时把 deleted 也带回来
  SELECT
    g.uuid                          AS group_uuid,
    g.product_package_uuid          AS combo_uuid,
    g.optional_count,
    g.delete_time                   AS group_delete_time,
    SUM(CASE WHEN g.delete_time = 0 THEN 1 ELSE 0 END)
      OVER (PARTITION BY g.product_package_uuid) AS active_group_count
  FROM `{project}`.`{dataset}`.`ttpos_product_package_group` g
),
filtered_groups AS (
  SELECT * FROM group_with_flag
  WHERE group_delete_time = 0 OR active_group_count = 0
),
item_with_flag AS (
  SELECT
    i.uuid                          AS item_uuid,
    i.product_package_group_uuid    AS group_uuid,
    i.related_uuid                  AS child_uuid,
    i.num                           AS child_num,
    i.delete_time                   AS item_delete_time,
    SUM(CASE WHEN i.delete_time = 0 THEN 1 ELSE 0 END)
      OVER (PARTITION BY i.product_package_group_uuid) AS active_item_count
  FROM `{project}`.`{dataset}`.`ttpos_product_package_group_item` i
),
filtered_items AS (
  SELECT * FROM item_with_flag
  WHERE item_delete_time = 0 OR active_item_count = 0
),
group_metric AS (
  -- candidate_count = 该 group 下的有效 slot 数（含同 child 的重复 slot）
  SELECT
    g.group_uuid,
    g.combo_uuid,
    g.optional_count,
    COUNT(i.item_uuid) AS candidate_count
  FROM filtered_groups g
  LEFT JOIN filtered_items i ON i.group_uuid = g.group_uuid
  GROUP BY g.group_uuid, g.combo_uuid, g.optional_count
)
SELECT
  gm.combo_uuid,
  i.child_uuid,
  i.child_num,
  -- weight = optional_count / candidate_count, 上限 1
  IF(gm.candidate_count = 0, 1.0,
     LEAST(1.0, gm.optional_count / gm.candidate_count)) AS weight
FROM group_metric gm
JOIN filtered_items i ON i.group_uuid = gm.group_uuid
"""
