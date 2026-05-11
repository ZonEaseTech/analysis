#!/usr/bin/env python3
"""
BigQuery SQL 模板库

预置常用的报表 SQL 模板，用于替代 n8n-scheduler 中的 MySQL 脚本。
所有模板使用 {project}, {dataset}, {start_ts}, {end_ts} 占位符。
"""

# ==================== 1. 销售业绩相关 ====================

# 基础销售业绩（ttpos_sale_bill）
SALES_REVENUE_SQL = """
SELECT 
  c.name AS store_name,
  ROUND(SUM(sb.amount), 2) AS total_turnover,
  ROUND(SUM(sb.payment_amount), 2) AS total_received,
  COUNT(*) AS order_count
FROM `{project}.{dataset}.ttpos_sale_bill` sb
CROSS JOIN `{project}.{dataset}.ttpos_company` c
WHERE sb.delete_time = 0 
  AND sb.status = 1
  AND sb.finish_time >= {start_ts} 
  AND sb.finish_time < {end_ts}
  AND c.delete_time = 0
GROUP BY c.name
LIMIT 1
"""

# 外卖平台订单（takeout_order）
TAKEOUT_ORDER_SUMMARY_SQL = """
SELECT 
  ROUND(SUM(subtotal), 2) AS platform_turnover,
  ROUND(SUM(platform_total), 2) AS platform_received,
  COUNT(*) AS order_count
FROM `{project}.{dataset}.ttpos_takeout_order`
WHERE delete_time = 0 
  AND order_state = 40
  AND platform IN ('grab', 'lineman', 'shopee')
  AND completed_time >= {start_ts} 
  AND completed_time < {end_ts}
"""

# 综合销售业绩（POS + 外卖平台） — 严格对齐 ttpos UI 销售业绩面板
#
# ttpos 源码:
#   - 子查询字段: ttpos-server-go/main/app/repository/statistics.go:84-128
#       sale_amount     = SUM(product_price + product_tax + service_fee + service_tax + payment_fee + extend_price)
#       received_amount = SUM(payment_amount - refund_amount - payment_balance)
#       business_amount = SUM(payment_amount - refund_amount - refund_payment_balance - product_tax - service_tax + refund_tax)
#   - 主查询聚合: ttpos-server-go/main/app/repository/statistics.go:130-182
#       total_sale_amount     = SUM(sale_amount)        UI「总销售额」 ← 本报表「营业额」对齐此口径
#       total_received_amount = SUM(received_amount)    UI「总实收」
#       total_order_num       = SUM(IF(is_meger=0,1,0)) 排除合单
#   - 测试营业时段排除: ttpos-server-go/main/app/repository/common.go:836-842 (ExcludeTestBusinessByBillSQL)
#   - UI 字段绑定:    ttpos-server-go/main/app/service/business.go:160-173
#       TotalSales = total_sale_amount
#
# 注意:
#   1. statistics_sale 已包含堂食 (is_takeout=0) + 自有外卖 (is_takeout=1)
#      第三方外卖 (grab/lineman/shopee) 不在 statistics_sale，仍走 takeout_order
#   2. takeout_order 字段用 platform_total（顾客实付，跟 ttpos statistics_takeout 后端一致）
#   3. 取消订单 state=60 算入 takeout 营业额（对齐 ttpos 后端，统一算法）
COMPREHENSIVE_SALES_SQL = """
WITH
-- POS 营业额/实收：per-bill COALESCE(statistics_sale, sale_bill)
-- statistics_sale 是 ttpos UI "门店统计" 的真实数据源 → 折前营业额（含未扣折扣，行业惯例）
-- 但 ttpos 后端有 2 类 bug，需要规避:
--   1. 异步生成 statistics_sale 失败 → 漏单 → 这里 sale_bill 兜底
--   2. 消息重试缺幂等 → 同 (sale_bill_uuid, sale_order_uuid, duty_no) 写 2+ 行 → 这里 ROW_NUMBER 去重
-- 订单数仍用 sale_bill 计数（事实数据源，不漏）
ss_dedup AS (
  -- 去重：同 (sale_bill_uuid, sale_order_uuid, duty_no, desk_uuid) 仅保留 1 行
  -- 不同 sale_order_uuid 是合法的拆单/多单，不算重复
  SELECT * EXCEPT(_rn)
  FROM (
    SELECT
      *,
      ROW_NUMBER() OVER (
        PARTITION BY sale_bill_uuid, sale_order_uuid, duty_no, desk_uuid
        ORDER BY uuid
      ) AS _rn
    FROM `{project}.{dataset}.ttpos_statistics_sale`
    WHERE delete_time = 0
      AND complete_time >= {start_ts} AND complete_time < {end_ts}
      {exclude_test_business_ss}
  )
  WHERE _rn = 1
),
ss_per_bill AS (
  SELECT
    sale_bill_uuid,
    SUM(product_price + product_tax + service_fee + service_tax + payment_fee + extend_price) AS ss_amount,
    SUM(payment_amount - refund_amount - payment_balance) AS ss_received
  FROM ss_dedup
  GROUP BY sale_bill_uuid
),
pos_per_bill AS (
  -- sale_bill 是订单事实表；statistics_sale 缺时用 sale_bill 兜底
  -- (兜底默认无退款，因为漏的单往往是 ttpos 后端 bug，未触发退款流程)
  SELECT
    COALESCE(ss.ss_amount, sb.amount) AS turnover,
    COALESCE(ss.ss_received, sb.payment_amount) AS received
  FROM `{project}.{dataset}.ttpos_sale_bill` sb
  LEFT JOIN ss_per_bill ss ON ss.sale_bill_uuid = sb.uuid
  WHERE sb.delete_time = 0 AND sb.status = 1
    AND sb.finish_time >= {start_ts} AND sb.finish_time < {end_ts}
    {exclude_test_business_sb}
),
pos_summary AS (
  SELECT
    ROUND(SUM(turnover), 2) AS turnover,
    ROUND(SUM(received), 2) AS received,
    COUNT(*) AS cnt
  FROM pos_per_bill
),
-- 第三方外卖 (grab/lineman/shopee) — 对齐 ttpos statistics_takeout
-- ttpos-server-go/main/app/repository/statistics_takeout.go:434
--   动态时间: state=40 已完成用 completed_time, 其他状态用 accepted_time
-- ttpos-server-go/main/app/repository/statistics_takeout.go:241
--   total_order_num = COUNT(DISTINCT uuid IF state IN (10,20,30,40,60))  含取消
-- 营业额: state IN (10,20,30,40,60) 含取消 — platform_total
-- 实收:   state IN (10,20,30,40)    不含取消 — platform_total
takeout_sales AS (
  SELECT
    ROUND(SUM(IF(order_state IN (10,20,30,40,60), platform_total, 0)), 2) AS turnover,
    ROUND(SUM(IF(order_state IN (10,20,30,40), platform_total, 0)), 2) AS received,
    COUNT(DISTINCT uuid) AS cnt
  FROM `{project}.{dataset}.ttpos_takeout_order`
  WHERE delete_time = 0
    AND order_state IN (10, 20, 30, 40, 60)
    AND platform IN ('grab', 'lineman', 'shopee')
    AND accepted_time > 0
    AND (
      CASE
        WHEN order_state = 40 AND completed_time > 0 THEN completed_time
        ELSE accepted_time
      END
    ) >= {start_ts}
    AND (
      CASE
        WHEN order_state = 40 AND completed_time > 0 THEN completed_time
        ELSE accepted_time
      END
    ) < {end_ts}
)
SELECT
  IFNULL(
    (SELECT JSON_EXTRACT_SCALAR(`values`, '$.store_code')
     FROM `{project}.{dataset}.ttpos_setting`
     WHERE `key` = 'store' AND delete_time = 0
     LIMIT 1), '') AS store_code,
  c.name AS store_name,
  IFNULL(ps.turnover, 0) + IFNULL(ts.turnover, 0) AS total_turnover,
  IFNULL(ps.received, 0) + IFNULL(ts.received, 0) AS total_received,
  IFNULL(ps.cnt, 0) + IFNULL(ts.cnt, 0) AS total_orders
FROM pos_summary ps
CROSS JOIN takeout_sales ts
CROSS JOIN `{project}.{dataset}.ttpos_company` c
WHERE c.delete_time = 0
LIMIT 1
"""


# ==================== 2. 物品消耗相关 ====================

# 物品消耗统计 — 跟 ttpos UI 一致，用 warehouse_out_form_item（销售出库单明细）
#
# ttpos 源码:
#   - 出库单 scene 枚举: ttpos-server-go/main/app/constant/warehouse.go:4-10
#       WarehouseOutFormSceneSales = 0   销售出库
#       WarehouseOutFormSceneAdjust = 1  调整出库
#       WarehouseOutFormSceneLoss = 2    损耗出库
#       WarehouseOutFormSceneLost = 3    丢失出库
#       WarehouseOutFormSceneDelete = 4  删除出库
#   - 物品消耗服务: ttpos-server-go/main/app/service/material.go:4179-4266
#
# 注意:
#   1. 是 `ttpos_warehouse_out_form_item`（出库表），不是 `ttpos_warehouse_form_item`
#      （那是入库表，scene 枚举完全不同：0 采购入库、1 添加入库、2 调整入库、3 退菜入库）。
#   2. revoke_time 在 item 表上，不需要 JOIN form 主表。
#   3. ttpos 实时报表会用 staff_shift_log 限定当前班次；月度报表无班次过滤。
#   4. 数据语义跟订单消耗 (sale_order_material) 不同：
#      - warehouse_out_form_item: 实际出库（含手工试餐/损耗/调整出库 但 scene=0 已过滤为纯销售）
#      - sale_order_material: 订单 BOM 自动展开（不含手工调整）
#      实测两者差异 +10~17%（warehouse_out 偏高，含数据修正出库）。
MATERIAL_CONSUMPTION_SQL = """
WITH consumption AS (
  SELECT
    wfi.material_uuid,
    SUM(wfi.num) AS total_num
  FROM `{project}.{dataset}.ttpos_warehouse_out_form_item` wfi
  WHERE wfi.delete_time = 0
    AND wfi.revoke_time = 0
    AND wfi.scene = 0
    AND wfi.material_uuid != 0
    AND wfi.create_time >= {start_ts}
    AND wfi.create_time < {end_ts}
  GROUP BY wfi.material_uuid
)
SELECT
  IFNULL(
    (SELECT JSON_EXTRACT_SCALAR(`values`, '$.store_code')
     FROM `{project}.{dataset}.ttpos_setting`
     WHERE `key` = 'store' AND delete_time = 0
     LIMIT 1), '') AS store_code,
  IFNULL(
    (SELECT JSON_EXTRACT_SCALAR(`values`, '$.store_name')
     FROM `{project}.{dataset}.ttpos_setting`
     WHERE `key` = 'store' AND delete_time = 0
     LIMIT 1),
    (SELECT name FROM `{project}.{dataset}.ttpos_company` WHERE delete_time = 0 LIMIT 1)
  ) AS store_name,
  c.material_uuid,
  COALESCE(
    JSON_EXTRACT_SCALAR(m.name, '$.zh'),
    JSON_EXTRACT_SCALAR(m.name, '$.en'),
    JSON_EXTRACT_SCALAR(m.name, '$.th'),
    '未知'
  ) AS material_name,
  ROUND(c.total_num, 2) AS total_num,
  COALESCE(
    JSON_EXTRACT_SCALAR(pu.name, '$.zh'),
    JSON_EXTRACT_SCALAR(pu.name, '$.en'),
    ''
  ) AS unit_name
FROM consumption c
-- 软删物料的历史消耗也要显示名字（BQ uuid 唯一，软删后只 1 行）
LEFT JOIN `{project}.{dataset}.ttpos_material` m
  ON m.uuid = c.material_uuid
LEFT JOIN `{project}.{dataset}.ttpos_material_unit` mu
  ON mu.material_uuid = m.uuid AND mu.is_default = 1
LEFT JOIN `{project}.{dataset}.ttpos_product_unit` pu
  ON pu.uuid = mu.unit_uuid
ORDER BY total_num DESC
"""

# BOM 推算的物品消耗（销量 × BOM 配方展开 → 物料）
#
# 语义：跟 ttpos UI 实时仓库的"销售出库"不同 — 这是按 BOM 理论推算
#       每销售 1 份商品规格 = 该规格 product_bom 关联 related_material 的 num
#       不含人工调整出库 / 损耗调整 / 试餐
#
# related_material 字段已经存的是基础单位的量（unit_uom == base_unit_uom），无需换算
#
# 销量口径与 BOM_PRODUCT_SALES_SQL 一致：
#   POS:    statistics_product where refund_time=0
#   外卖:   takeout_order_item + order_state IN (10,20,30,40,60), accepted_time 落在区间
#
# 物料展开两条路径（合并不去重，按总数加和）:
#   (a) related_material 直挂 product_bom（related_uuid = pb.uuid）
#   (b) product_bom 关联成本卡（pb.product_bom_card_uuid > 0），related_material 挂在卡上
MATERIAL_BOM_CONSUMPTION_SQL = """
WITH
shop_sales AS (
  -- 堂食销量按 (package, bom_uuid) 拆
  SELECT
    sp.product_package_uuid,
    sp.product_bom_uuid,
    SUM(sp.product_num) AS sale_num
  FROM `{project}.{dataset}.ttpos_statistics_product` sp
  WHERE sp.delete_time = 0
    AND sp.refund_time = 0
    AND sp.product_bom_uuid > 0
    AND sp.complete_time >= {start_ts}
    AND sp.complete_time < {end_ts}
  GROUP BY sp.product_package_uuid, sp.product_bom_uuid
),
takeout_pkg_sales AS (
  -- 外卖销量只到 package 级（数据模型限制）
  SELECT
    toi.ttpos_product_package_uuid AS product_package_uuid,
    SUM(toi.quantity) AS sale_num
  FROM `{project}.{dataset}.ttpos_takeout_order_item` toi
  JOIN `{project}.{dataset}.ttpos_takeout_order` t
    ON t.uuid = toi.takeout_order_uuid AND t.delete_time = 0
  WHERE toi.delete_time = 0
    AND toi.ttpos_product_package_uuid > 0
    AND t.order_state IN (10, 20, 30, 40, 60)
    AND t.accepted_time > 0
    AND t.accepted_time >= {start_ts}
    AND t.accepted_time < {end_ts}
  GROUP BY product_package_uuid
),
shop_pkg_total AS (
  -- 每个 package 的堂食总销量（用于按堂食规格销量比例分摊外卖销量）
  SELECT
    product_package_uuid,
    SUM(sale_num) AS pkg_total
  FROM shop_sales
  GROUP BY product_package_uuid
),
takeout_allocated AS (
  -- 把外卖销量按"堂食各规格销量占比"分摊到每个 product_bom_uuid
  -- weight = shop_sales.sale_num / shop_pkg_total.pkg_total
  -- 边缘 case：package 只有外卖没堂食 → 没法分摊，丢失（数据上很罕见）
  SELECT
    s.product_bom_uuid,
    SUM(t.sale_num * s.sale_num / pt.pkg_total) AS sale_num
  FROM shop_sales s
  JOIN shop_pkg_total pt USING (product_package_uuid)
  JOIN takeout_pkg_sales t USING (product_package_uuid)
  WHERE pt.pkg_total > 0
  GROUP BY s.product_bom_uuid
),
shop_sales_by_bom AS (
  SELECT product_bom_uuid, SUM(sale_num) AS sale_num
  FROM shop_sales
  GROUP BY product_bom_uuid
),
sales AS (
  -- 合并堂食实际销量 + 外卖分摊销量
  SELECT
    COALESCE(s.product_bom_uuid, ta.product_bom_uuid) AS product_bom_uuid,
    IFNULL(s.sale_num, 0) + IFNULL(ta.sale_num, 0) AS total_qty
  FROM shop_sales_by_bom s
  FULL OUTER JOIN takeout_allocated ta USING (product_bom_uuid)
),
-- (a) related_material 直挂 product_bom
direct_bom AS (
  SELECT
    pb.uuid AS product_bom_uuid,
    rm.material_uuid,
    rm.num AS num_per_unit
  FROM `{project}.{dataset}.ttpos_product_bom` pb
  JOIN `{project}.{dataset}.ttpos_related_material` rm
    ON rm.related_uuid = pb.uuid AND rm.delete_time = 0
  WHERE pb.delete_time = 0
    AND rm.material_uuid > 0
),
-- (b) product_bom -> 成本卡 -> related_material
card_bom AS (
  SELECT
    pb.uuid AS product_bom_uuid,
    rm.material_uuid,
    rm.num AS num_per_unit
  FROM `{project}.{dataset}.ttpos_product_bom` pb
  JOIN `{project}.{dataset}.ttpos_related_material` rm
    ON rm.related_uuid = pb.product_bom_card_uuid AND rm.delete_time = 0
  WHERE pb.delete_time = 0
    AND pb.product_bom_card_uuid > 0
    AND rm.material_uuid > 0
),
expanded AS (
  SELECT product_bom_uuid, material_uuid, num_per_unit FROM direct_bom
  UNION ALL
  SELECT product_bom_uuid, material_uuid, num_per_unit FROM card_bom
),
consumption AS (
  SELECT
    e.material_uuid,
    SUM(s.total_qty * e.num_per_unit) AS total_num
  FROM sales s
  JOIN expanded e USING (product_bom_uuid)
  GROUP BY e.material_uuid
)
SELECT
  IFNULL(
    (SELECT JSON_EXTRACT_SCALAR(`values`, '$.store_code')
     FROM `{project}.{dataset}.ttpos_setting`
     WHERE `key` = 'store' AND delete_time = 0
     LIMIT 1), '') AS store_code,
  IFNULL(
    (SELECT JSON_EXTRACT_SCALAR(`values`, '$.store_name')
     FROM `{project}.{dataset}.ttpos_setting`
     WHERE `key` = 'store' AND delete_time = 0
     LIMIT 1),
    (SELECT name FROM `{project}.{dataset}.ttpos_company` WHERE delete_time = 0 LIMIT 1)
  ) AS store_name,
  c.material_uuid,
  COALESCE(
    JSON_EXTRACT_SCALAR(m.name, '$.zh'),
    JSON_EXTRACT_SCALAR(m.name, '$.en'),
    JSON_EXTRACT_SCALAR(m.name, '$.th'),
    '未知'
  ) AS material_name,
  ROUND(c.total_num, 4) AS total_num,
  COALESCE(
    JSON_EXTRACT_SCALAR(pu.name, '$.zh'),
    JSON_EXTRACT_SCALAR(pu.name, '$.en'),
    ''
  ) AS unit_name
FROM consumption c
-- 软删物料的 BOM 推算也要显示名字（BQ uuid 唯一）
LEFT JOIN `{project}.{dataset}.ttpos_material` m
  ON m.uuid = c.material_uuid
LEFT JOIN `{project}.{dataset}.ttpos_material_unit` mu
  ON mu.material_uuid = m.uuid AND mu.is_default = 1
LEFT JOIN `{project}.{dataset}.ttpos_product_unit` pu
  ON pu.uuid = mu.unit_uuid
WHERE c.total_num > 0
ORDER BY total_num DESC
"""


# 指定原料的消耗统计（按 material.code）
SPECIFIC_MATERIAL_CONSUMPTION_SQL = """
WITH material_uuids AS (
  SELECT uuid 
  FROM `{project}.{dataset}.ttpos_material` 
  WHERE delete_time = 0 
    AND code IN ({material_codes})
),
pos_consumption AS (
  SELECT 
    som.material_uuid,
    SUM(som.num) AS num
  FROM `{project}.{dataset}.ttpos_sale_order_material` som
  JOIN `{project}.{dataset}.ttpos_sale_bill` sb 
    ON sb.uuid = som.sale_bill_uuid AND sb.delete_time = 0
  WHERE som.delete_time = 0
    AND som.material_uuid IN (SELECT uuid FROM material_uuids)
    AND sb.status = 1
    AND sb.finish_time >= {start_ts} 
    AND sb.finish_time < {end_ts}
  GROUP BY som.material_uuid
),
takeout_consumption AS (
  SELECT 
    tom.material_uuid,
    SUM(tom.num) AS num
  FROM `{project}.{dataset}.ttpos_takeout_order_material` tom
  JOIN `{project}.{dataset}.ttpos_takeout_order` tko 
    ON tko.uuid = tom.takeout_order_uuid AND tko.delete_time = 0
  WHERE tom.delete_time = 0
    AND tom.material_uuid IN (SELECT uuid FROM material_uuids)
    AND tko.order_state = 40
    AND tko.completed_time > 0
    AND tko.completed_time >= {start_ts} 
    AND tko.completed_time < {end_ts}
  GROUP BY tom.material_uuid
)
SELECT 
  COALESCE(pc.num, 0) + COALESCE(tc.num, 0) AS total_consumption
FROM (SELECT 1) dummy
LEFT JOIN pos_consumption pc ON TRUE
LEFT JOIN takeout_consumption tc ON TRUE
"""


# ==================== 3. BOM商品销量相关 ====================

# BOM 商品销量（按规格拆） — 算法跟 ttpos RankProduct/RankTakeoutProduct 一致，
# 但 GROUP BY 加上 product_bom_uuid（具体规格行）以便区分 "香脆鸡翅(大份)" 和 "香脆鸡翅(小份)"
#
# ttpos 源码:
#   - shop:    ttpos-server-go/main/app/repository/statistics.go:1245-1253 (RankProduct)
#              SUM(sp.product_num) AS sale_num + WHERE refund_time=0 + GROUP BY product_package_uuid
#              （ttpos UI 是 package 级合并，本报表加 product_bom_uuid 拆出规格）
#   - takeout: ttpos-server-go/main/app/repository/statistics.go:451-502 (RankTakeoutProduct)
#              order_state IN (10,20,30,40,60) + accepted_time > 0
#
# 商品名称格式:
#   {商品包名}(规格名)   有规格 → "香脆鸡翅(大份)"
#   {商品包名}            无规格 → "香脆鸡翅"
#
# product_bom 行类型:
#   - product_flavor_uuid > 0  规格行（大份/小份/原味/微辣）
#   - product_sauce_uuid > 0   小料行（加芝士/加蛋）
#   - 两者都 = 0               主行（极少数无规格商品）
#
# 已设/未设 BOM 判定（规格级粒度）:
#   当前 product_bom 行 EXISTS related_material 直挂
#   OR pb.product_bom_card_uuid > 0 AND 该卡 EXISTS related_material
#
# 数据来源限制:
#   - statistics_product 有 product_bom_uuid，堂食销量可以精确到规格
#   - takeout_order_item 只有 ttpos_product_package_uuid，无规格 uuid
#     → 外卖销量只能聚到 package 级（bom_uuid=0），规格名留空
#     → 报表上外卖部分会单独显示一行 "{包名}"（不带规格后缀）
BOM_PRODUCT_SALES_SQL = """
WITH
shop_sales AS (
  -- 堂食销量（按规格拆，含规格行+小料行的 product_bom_uuid）
  SELECT
    sp.product_package_uuid,
    sp.product_bom_uuid,
    SUM(sp.product_num) AS sale_num
  FROM `{project}.{dataset}.ttpos_statistics_product` sp
  WHERE sp.delete_time = 0
    AND sp.refund_time = 0
    AND sp.complete_time >= {start_ts}
    AND sp.complete_time < {end_ts}
  GROUP BY sp.product_package_uuid, sp.product_bom_uuid
),
takeout_sales AS (
  -- 外卖销量：只能聚到 package（数据模型限制）→ product_bom_uuid 设为 0
  SELECT
    toi.ttpos_product_package_uuid AS product_package_uuid,
    CAST(0 AS NUMERIC) AS product_bom_uuid,
    SUM(toi.quantity) AS sale_num
  FROM `{project}.{dataset}.ttpos_takeout_order_item` toi
  JOIN `{project}.{dataset}.ttpos_takeout_order` t
    ON t.uuid = toi.takeout_order_uuid AND t.delete_time = 0
  WHERE toi.delete_time = 0
    AND toi.ttpos_product_package_uuid > 0
    AND t.order_state IN (10, 20, 30, 40, 60)
    AND t.accepted_time > 0
    AND t.accepted_time >= {start_ts}
    AND t.accepted_time < {end_ts}
  GROUP BY product_package_uuid
),
merged AS (
  SELECT
    COALESCE(s.product_package_uuid, t.product_package_uuid) AS product_package_uuid,
    COALESCE(s.product_bom_uuid, t.product_bom_uuid) AS product_bom_uuid,
    IFNULL(s.sale_num, 0) + IFNULL(t.sale_num, 0) AS total_qty
  FROM shop_sales s
  FULL OUTER JOIN takeout_sales t
    USING (product_package_uuid, product_bom_uuid)
),
-- 规格级 has_bom 判定：当前 product_bom 行有 related_material（直挂或通过成本卡）
bom_set AS (
  SELECT DISTINCT pb.uuid AS pb_uuid
  FROM `{project}.{dataset}.ttpos_product_bom` pb
  WHERE pb.delete_time = 0
    AND (
      EXISTS (
        SELECT 1 FROM `{project}.{dataset}.ttpos_related_material` rm
        WHERE rm.related_uuid = pb.uuid AND rm.delete_time = 0
      )
      OR (
        pb.product_bom_card_uuid > 0
        AND EXISTS (
          SELECT 1 FROM `{project}.{dataset}.ttpos_related_material` rm
          WHERE rm.related_uuid = pb.product_bom_card_uuid AND rm.delete_time = 0
        )
      )
    )
),
-- package 级 has_bom 判定（用于外卖部分 bom_uuid=0 的行）：
-- package 下任意一个 product_bom 行有 BOM → 视为已设置
package_has_bom AS (
  SELECT DISTINCT pb.product_package_uuid AS pp_uuid
  FROM `{project}.{dataset}.ttpos_product_bom` pb
  JOIN bom_set b ON b.pb_uuid = pb.uuid
  WHERE pb.delete_time = 0
)
SELECT
  IFNULL(
    (SELECT JSON_EXTRACT_SCALAR(`values`, '$.store_code')
     FROM `{project}.{dataset}.ttpos_setting`
     WHERE `key` = 'store' AND delete_time = 0
     LIMIT 1),
    ''
  ) AS store_code,
  IFNULL(
    (SELECT JSON_EXTRACT_SCALAR(`values`, '$.store_name')
     FROM `{project}.{dataset}.ttpos_setting`
     WHERE `key` = 'store' AND delete_time = 0
     LIMIT 1),
    (SELECT name FROM `{project}.{dataset}.ttpos_company` WHERE delete_time = 0 LIMIT 1)
  ) AS store_name,
  m.product_package_uuid,
  m.product_bom_uuid,
  -- 包名（简体）
  COALESCE(
    JSON_EXTRACT_SCALAR(pp.name, '$.zh'),
    JSON_EXTRACT_SCALAR(pp.name, '$.en'),
    JSON_EXTRACT_SCALAR(pp.name, '$.th'),
    '未知'
  ) AS package_name,
  -- 规格名（仅 flavor 行；sauce 行也带名称，作为加料一并展示）
  COALESCE(
    JSON_EXTRACT_SCALAR(pf.name, '$.zh'),
    JSON_EXTRACT_SCALAR(pf.name, '$.en'),
    JSON_EXTRACT_SCALAR(ps.name, '$.zh'),
    JSON_EXTRACT_SCALAR(ps.name, '$.en'),
    ''
  ) AS spec_name,
  -- bom 类型：1=flavor 规格 2=sauce 小料 0=主商品（无规格无小料）
  CASE
    WHEN pb.product_flavor_uuid > 0 THEN 1
    WHEN pb.product_sauce_uuid > 0 THEN 2
    ELSE 0
  END AS bom_type,
  ROUND(m.total_qty, 2) AS total_qty,
  -- 外卖（bom_uuid=0）按 package 级判定，堂食按规格级判定
  CASE
    WHEN m.product_bom_uuid = 0 THEN IF(phb.pp_uuid IS NOT NULL, 1, 0)
    ELSE IF(b.pb_uuid IS NOT NULL, 1, 0)
  END AS has_bom,
  -- 商品分类
  COALESCE(
    JSON_EXTRACT_SCALAR(pc.name, '$.zh'),
    JSON_EXTRACT_SCALAR(pc.name, '$.en'),
    ''
  ) AS category_name
FROM merged m
-- 名称 JOIN 不过滤 delete_time：软删商品的历史销售也要能正常显示名字
-- (BQ uuid 唯一，软删后表里仍只有 1 行，不会重复)
LEFT JOIN `{project}.{dataset}.ttpos_product_package` pp
  ON pp.uuid = m.product_package_uuid
LEFT JOIN `{project}.{dataset}.ttpos_product_bom` pb
  ON pb.uuid = m.product_bom_uuid
LEFT JOIN `{project}.{dataset}.ttpos_product_flavor` pf
  ON pf.uuid = pb.product_flavor_uuid
LEFT JOIN `{project}.{dataset}.ttpos_product_sauce` ps
  ON ps.uuid = pb.product_sauce_uuid
-- has_bom 判定保留 delete_time=0：软删的 bom 算 "未设置 BOM"
LEFT JOIN bom_set b
  ON b.pb_uuid = m.product_bom_uuid
LEFT JOIN package_has_bom phb
  ON phb.pp_uuid = m.product_package_uuid
-- 商品分类
LEFT JOIN `{project}.{dataset}.ttpos_product_category` pc
  ON pc.uuid = pp.category_uuid AND pc.delete_time = 0
WHERE m.total_qty > 0
ORDER BY has_bom DESC, total_qty DESC
"""


# ==================== 4. 单品日销量相关 ====================

# 单品日销量（堂食 + 外卖）
DAILY_ITEM_SALES_SQL = """
WITH 
-- 堂食销量（sale_order_product）
dinein_sales AS (
  SELECT
    DATE(TIMESTAMP_SECONDS(sb.finish_time), 'Asia/Bangkok') AS sale_date,
    sop.product_package_uuid,
    JSON_EXTRACT_SCALAR(pp.name, '$.zh') AS product_name_zh,
    JSON_EXTRACT_SCALAR(pp.name, '$.en') AS product_name_en,
    SUM(CASE
      WHEN sop.product_type = 0 THEN sop.num
      WHEN sop.product_type = 2 THEN sop.num * sop.copy_num * IF(sop.unit_num = 0, 1, sop.unit_num)
      ELSE 0
    END) AS qty
  FROM `{project}.{dataset}.ttpos_sale_order_product` sop
  JOIN `{project}.{dataset}.ttpos_sale_bill` sb 
    ON sb.uuid = sop.sale_bill_uuid AND sb.delete_time = 0
  JOIN `{project}.{dataset}.ttpos_product_package` pp 
    ON pp.uuid = sop.product_package_uuid AND pp.delete_time = 0
  WHERE sop.delete_time = 0
    AND sop.cancel_time = 0
    AND sop.gift_time = 0
    AND sop.status = 1
    AND sop.product_type <> 1
    AND sb.status = 1
    AND sb.finish_time >= {start_ts}
    AND sb.finish_time < {end_ts}
  GROUP BY sale_date, sop.product_package_uuid, pp.name
),
-- 外卖销量（takeout_order_item）
takeout_sales AS (
  SELECT
    DATE(TIMESTAMP_SECONDS(
      CASE 
        WHEN tko.order_state = 40 THEN tko.completed_time
        ELSE tko.accepted_time
      END
    ), 'Asia/Bangkok') AS sale_date,
    toi.ttpos_product_package_uuid AS product_package_uuid,
    JSON_EXTRACT_SCALAR(pp.name, '$.zh') AS product_name_zh,
    JSON_EXTRACT_SCALAR(pp.name, '$.en') AS product_name_en,
    SUM(toi.quantity) AS qty
  FROM `{project}.{dataset}.ttpos_takeout_order_item` toi
  JOIN `{project}.{dataset}.ttpos_takeout_order` tko
    ON tko.uuid = toi.takeout_order_uuid AND tko.delete_time = 0
  LEFT JOIN `{project}.{dataset}.ttpos_product_package` pp
    ON pp.uuid = toi.ttpos_product_package_uuid AND pp.delete_time = 0
  WHERE toi.delete_time = 0
    AND tko.order_state IN (10, 20, 30, 40, 60)
    AND (
      (tko.order_state = 40 AND tko.completed_time > 0
       AND tko.completed_time >= {start_ts} AND tko.completed_time < {end_ts})
      OR
      (tko.order_state != 40 AND tko.accepted_time >= {start_ts}
       AND tko.accepted_time < {end_ts})
    )
  GROUP BY sale_date, toi.ttpos_product_package_uuid, pp.name
),
-- 合并
combined AS (
  SELECT * FROM dinein_sales
  UNION ALL
  SELECT * FROM takeout_sales
)
SELECT 
  sale_date,
  product_package_uuid,
  COALESCE(product_name_zh, product_name_en, '未知') AS product_name,
  ROUND(SUM(qty), 2) AS total_qty
FROM combined
GROUP BY sale_date, product_package_uuid, product_name_zh, product_name_en
HAVING total_qty > 0
ORDER BY sale_date DESC, total_qty DESC
"""


# ==================== 5. 库存相关 ====================

# 采购入库统计
PURCHASE_IN_SQL = """
SELECT 
  ROUND(SUM(num), 2) AS total_in
FROM `{project}.{dataset}.ttpos_warehouse_in_out_log`
WHERE delete_time = 0
  AND log_type = 0
  AND scene = 0
  AND material_uuid IN (SELECT uuid FROM `{project}.{dataset}.ttpos_material` WHERE code IN ({material_codes}))
  AND create_time >= {start_ts} 
  AND create_time < {end_ts}
"""

# 原料涉及的销售金额（通过BOM关联）
MATERIAL_RELATED_SALES_SQL = """
WITH material_uuids AS (
  SELECT uuid 
  FROM `{project}.{dataset}.ttpos_material` 
  WHERE delete_time = 0 
    AND code IN ({material_codes})
)
SELECT 
  ROUND(SUM(sop.total_price * sop.num), 2) AS total_sales
FROM `{project}.{dataset}.ttpos_sale_order_product` sop
JOIN `{project}.{dataset}.ttpos_sale_bill` sb 
  ON sb.uuid = sop.sale_bill_uuid AND sb.delete_time = 0
WHERE sop.delete_time = 0
  AND sop.cancel_time = 0
  AND sop.gift_time = 0
  AND sop.status = 1
  AND sop.product_type <> 1
  AND sb.status = 1
  AND sb.finish_time >= {start_ts} 
  AND sb.finish_time < {end_ts}
  AND EXISTS (
    SELECT 1
    FROM `{project}.{dataset}.ttpos_sale_order_product_bom` sopb
    INNER JOIN `{project}.{dataset}.ttpos_product_bom` pb 
      ON pb.uuid = sopb.product_bom_uuid AND pb.delete_time = 0
    WHERE sopb.sale_order_product_uuid = sop.uuid 
      AND sopb.delete_time = 0
      AND (
        -- 成本卡关联
        (sopb.is_flavor_bom = 1 AND pb.product_bom_card_uuid > 0
          AND EXISTS (
            SELECT 1 FROM `{project}.{dataset}.ttpos_related_material` rm
            WHERE rm.delete_time = 0 
              AND rm.related_uuid = pb.product_bom_card_uuid
              AND rm.material_uuid IN (SELECT uuid FROM material_uuids)
          ))
        -- 规格BOM直挂
        OR
        (sopb.is_flavor_bom = 1
          AND EXISTS (
            SELECT 1 FROM `{project}.{dataset}.ttpos_related_material` rm
            WHERE rm.delete_time = 0 
              AND rm.related_uuid = pb.uuid
              AND rm.material_uuid IN (SELECT uuid FROM material_uuids)
          ))
      )
  )
"""


# ==================== 6. 外卖营业额相关 ====================

# 外卖营业额统计（POS 侧 + 外卖平台侧）
# 与 payment.sh 口径一致：
#   - POS 侧：账单中至少有一笔已支付记录的支付方式名匹配 robinhood|grab|lineman|shopee
#   - 平台侧：takeout_order 中 platform IN ('grab','lineman','shopee') 且 order_state=40
# 注：旧版本曾把 dining_method=1（打包）/ bill_type=2 / order_source_uuid>0 都算外卖，
#     会把堂食打包带走的订单虚增到外卖里。现已收窄到只看支付方式。
TAKEOUT_REVENUE_SQL = """
WITH finished_bills AS (
  SELECT
    sb.uuid AS bill_uuid,
    sb.amount AS bill_amount,
    sb.payment_amount AS bill_payment_amount
  FROM `{project}`.`{dataset}`.`ttpos_sale_bill` AS sb
  WHERE sb.delete_time = 0
    AND sb.status = 1
    AND sb.finish_time >= {start_ts}
    AND sb.finish_time < {end_ts}
),

bill_takeout AS (
  SELECT
    fb.bill_uuid,
    EXISTS (
      SELECT 1
      FROM `{project}`.`{dataset}`.`ttpos_payment_order` AS po
      INNER JOIN `{project}`.`{dataset}`.`ttpos_sale_order` AS so
        ON so.uuid = po.related_uuid
        AND po.related_type = 0
        AND so.delete_time = 0
      WHERE so.sale_bill_uuid = fb.bill_uuid
        AND po.delete_time = 0
        AND po.status = 1
        AND REGEXP_CONTAINS(
          LOWER(REPLACE(IFNULL(po.payment_method_name, ''), ' ', '')),
          r'robinhood|grab|lineman|shopee'
        )
    ) AS is_takeout
  FROM finished_bills AS fb
),

takeout_platform AS (
  -- 外卖平台侧（takeout_order）：Grab / Lineman / Shopee 已完成订单
  SELECT
    SUM(subtotal) AS platform_turnover,
    SUM(platform_total) AS platform_received
  FROM `{project}`.`{dataset}`.`ttpos_takeout_order`
  WHERE delete_time = 0
    AND order_state = 40
    AND platform IN ('grab', 'lineman', 'shopee')
    AND completed_time >= {start_ts}
    AND completed_time < {end_ts}
)

SELECT
  CAST(c.uuid AS STRING) AS store_uuid,
  IFNULL(cs.erpnext_company_abbr, '') AS store_code_abbr,
  c.name AS store_name,
  ROUND(SUM(fb.bill_amount), 2) AS total_turnover,
  ROUND(SUM(fb.bill_payment_amount), 2) AS total_actual_received,
  ROUND(SUM(IF(bt.is_takeout, fb.bill_amount, 0)) + IFNULL(MAX(tp.platform_turnover), 0), 2) AS takeout_turnover,
  ROUND(SUM(IF(bt.is_takeout, fb.bill_payment_amount, 0)) + IFNULL(MAX(tp.platform_received), 0), 2) AS takeout_actual_received
FROM finished_bills AS fb
INNER JOIN bill_takeout AS bt ON bt.bill_uuid = fb.bill_uuid
CROSS JOIN `{project}`.`{dataset}`.`ttpos_company` AS c
INNER JOIN `{project}`.`{dataset}`.`ttpos_company_setting` AS cs
  ON cs.company_uuid = c.uuid AND cs.delete_time = 0
CROSS JOIN takeout_platform AS tp
WHERE c.delete_time = 0
GROUP BY c.uuid, cs.erpnext_company_abbr, c.name
"""


# ==================== 7. 订单国籍相关 ====================

# 订单国籍明细（POS 订单 + 国籍 + 商品明细）
ORDER_NATIONALITY_SQL = """
WITH
order_items AS (
  SELECT
    sop.sale_bill_uuid,
    STRING_AGG(COALESCE(
      JSON_EXTRACT_SCALAR(pp.name, '$.zh'),
      JSON_EXTRACT_SCALAR(pp.name, '$.en'),
      JSON_EXTRACT_SCALAR(pp.name, '$.th'),
      '未知'
    ), ', ') AS items
  FROM `{project}.{dataset}.ttpos_sale_order_product` sop
  JOIN `{project}.{dataset}.ttpos_product_package` pp
    ON pp.uuid = sop.product_package_uuid AND pp.delete_time = 0
  WHERE sop.delete_time = 0
    AND sop.cancel_time = 0
    AND sop.product_type IN (0, 2)
  GROUP BY sop.sale_bill_uuid
)
SELECT
  sb.order_no AS order_number,
  sb.finish_time AS order_time,
  COALESCE(mln.zh_name, mln.en_name, mln.th_name, '未知') AS nationality,
  ROUND(sb.payment_amount, 2) AS received_amount,
  COALESCE(oi.items, '') AS order_details
FROM `{project}.{dataset}.ttpos_sale_bill` sb
LEFT JOIN `{project}.{dataset}.ttpos_nationality` n
  ON n.uuid = sb.nationality_uuid AND n.delete_time = 0
LEFT JOIN `{project}.{dataset}.ttpos_multi_language_name` mln
  ON mln.uuid = n.multi_language_name_uuid AND mln.delete_time = 0
LEFT JOIN order_items oi ON oi.sale_bill_uuid = sb.uuid
WHERE sb.delete_time = 0
  AND sb.status = 1
  AND sb.finish_time >= {start_ts}
  AND sb.finish_time < {end_ts}
ORDER BY sb.finish_time DESC
"""


# ==================== 8. 支付方式明细 ====================
#
# 按门店 × 支付方式聚合，给客户做对账（跟支付平台/银行流水核对）。
#
# ttpos 源码:
#   - POS:    ttpos-server-go/main/app/repository/statistics.go:671-693 (CountPayment)
#             表 statistics_payment, JOIN payment_method 取支付方式名
#   - 外卖:    ttpos-server-go/main/app/repository/statistics_takeout.go:295-380 (CountTakeoutPayment)
#             takeout_order.platform 直接当作支付方式名（'grab' → 'Grab'）
#
# 注意:
#   1. statistics_payment 跟 statistics_sale 一样有偶发"消息重试 + 缺幂等" 重复行 bug,
#      需要用 ROW_NUMBER 按 (sale_bill_uuid, sale_order_uuid, payment_method_uuid) 去重。
#      53 店实测 1 家有 1 组重复（影响 ~0.001%），不去重会金额翻倍。
#   2. payment_method.payment_name 是纯字符串（不是多语言 JSON），直接取。
#   3. 外卖 platform 跟 POS payment_method 名称可能重复（如 LINE MAN 在 payment_method 表
#      也有 source=1 的条目），但语义不同：POS 侧是顾客在 ttpos 内选 LINE MAN 收款，
#      外卖侧是 LINE MAN 平台 API 同步过来的订单。两路数据无关联，不去重。
#   4. 退款金额: POS 侧从 statistics_payment.refund_amount, 外卖从 platform_total[state=60]
PAYMENT_BREAKDOWN_SQL = """
WITH
pos_payment_dedup AS (
  -- 跟 ss_dedup 同思路：消息重试导致同 (bill, order, method) 写多行，去重
  SELECT * EXCEPT(_rn)
  FROM (
    SELECT
      sale_bill_uuid,
      sale_order_uuid,
      payment_method_uuid,
      payment_amount,
      refund_amount,
      ROW_NUMBER() OVER (
        PARTITION BY sale_bill_uuid, sale_order_uuid, payment_method_uuid
        ORDER BY uuid
      ) AS _rn
    FROM `{project}.{dataset}.ttpos_statistics_payment`
    WHERE delete_time = 0
      AND complete_time >= {start_ts} AND complete_time < {end_ts}
      {exclude_test_business_ss}
  )
  WHERE _rn = 1
),
pos_payment AS (
  SELECT
    pm.payment_name AS method_name,
    'POS' AS channel,
    COUNT(*) AS bill_cnt,
    ROUND(SUM(spp.payment_amount), 2) AS total_amount,
    ROUND(SUM(spp.refund_amount), 2) AS total_refund
  FROM pos_payment_dedup spp
  LEFT JOIN `{project}.{dataset}.ttpos_payment_method` pm
    ON pm.uuid = spp.payment_method_uuid
  GROUP BY method_name
),
takeout_payment AS (
  -- 外卖平台直接收款（钱在平台手里，对账要跟平台结算单核）
  -- 跟销售业绩 sheet 实收口径一致：只算 state IN (10,20,30,40)，不含取消订单（state=60）
  -- 取消订单语义是"顾客下单后取消"，钱根本没结算给商家，不算"退款"
  SELECT
    CASE platform
      WHEN 'grab' THEN 'Grab'
      WHEN 'lineman' THEN 'LINE MAN'
      WHEN 'shopee' THEN 'Shopee'
      ELSE platform
    END AS method_name,
    '外卖平台' AS channel,
    COUNT(*) AS bill_cnt,
    ROUND(SUM(platform_total), 2) AS total_amount,
    CAST(0 AS NUMERIC) AS total_refund
  FROM `{project}.{dataset}.ttpos_takeout_order`
  WHERE delete_time = 0
    AND order_state IN (10, 20, 30, 40)
    AND platform IN ('grab', 'lineman', 'shopee')
    AND accepted_time > 0
    AND (
      CASE WHEN order_state = 40 AND completed_time > 0 THEN completed_time
           ELSE accepted_time END
    ) >= {start_ts}
    AND (
      CASE WHEN order_state = 40 AND completed_time > 0 THEN completed_time
           ELSE accepted_time END
    ) < {end_ts}
  GROUP BY method_name
),
all_payments AS (
  SELECT * FROM pos_payment
  UNION ALL
  SELECT * FROM takeout_payment
)
SELECT
  IFNULL(
    (SELECT JSON_EXTRACT_SCALAR(`values`, '$.store_code')
     FROM `{project}.{dataset}.ttpos_setting`
     WHERE `key` = 'store' AND delete_time = 0 LIMIT 1), '') AS store_code,
  IFNULL(
    (SELECT JSON_EXTRACT_SCALAR(`values`, '$.store_name')
     FROM `{project}.{dataset}.ttpos_setting`
     WHERE `key` = 'store' AND delete_time = 0 LIMIT 1),
    (SELECT name FROM `{project}.{dataset}.ttpos_company` WHERE delete_time = 0 LIMIT 1)
  ) AS store_name,
  IFNULL(method_name, '未知') AS method_name,
  channel,
  bill_cnt,
  total_amount,
  total_refund,
  ROUND(total_amount - total_refund, 2) AS net_amount
FROM all_payments
WHERE bill_cnt > 0 OR total_amount > 0 OR total_refund > 0
ORDER BY channel, total_amount DESC
"""


# ==================== 9. 支付方式明细（按天） ====================
#
# 跟 PAYMENT_BREAKDOWN_SQL 同源，但按 (日期, 支付方式, 渠道) 拆开 → 给客户做日报对账。
# 跨 sheet 校验：SUM(每店每天每方式净收) = PAYMENT_BREAKDOWN_SQL 月度净收。
#
# 时区: BKK (+07:00) — 跟 ttpos 业务时区对齐，月度边界 ttpos 按 BKK 算
#
# ttpos 源码:
#   - POS:    ttpos-server-go/main/app/repository/statistics.go:723-797 (CountPaymentDays)
#             去 timezone 后按 DATE 分组
#   - 外卖:    statistics_takeout.go 同样动态时间字段 + 时区转换
PAYMENT_BREAKDOWN_DAILY_SQL = """
WITH
pos_payment_dedup AS (
  SELECT * EXCEPT(_rn)
  FROM (
    SELECT
      sale_bill_uuid,
      sale_order_uuid,
      payment_method_uuid,
      payment_amount,
      refund_amount,
      complete_time,
      ROW_NUMBER() OVER (
        PARTITION BY sale_bill_uuid, sale_order_uuid, payment_method_uuid
        ORDER BY uuid
      ) AS _rn
    FROM `{project}.{dataset}.ttpos_statistics_payment`
    WHERE delete_time = 0
      AND complete_time >= {start_ts} AND complete_time < {end_ts}
      {exclude_test_business_ss}
  )
  WHERE _rn = 1
),
pos_payment AS (
  SELECT
    DATE(TIMESTAMP_SECONDS(spp.complete_time), 'Asia/Bangkok') AS pay_date,
    pm.payment_name AS method_name,
    'POS' AS channel,
    COUNT(*) AS bill_cnt,
    ROUND(SUM(spp.payment_amount), 2) AS total_amount,
    ROUND(SUM(spp.refund_amount), 2) AS total_refund
  FROM pos_payment_dedup spp
  LEFT JOIN `{project}.{dataset}.ttpos_payment_method` pm
    ON pm.uuid = spp.payment_method_uuid
  GROUP BY pay_date, method_name
),
takeout_payment AS (
  -- 外卖：动态时间字段（state=40 用 completed_time，其他用 accepted_time）
  -- 跟销售业绩 sheet 实收口径一致：只算 state IN (10,20,30,40)，不含取消订单
  SELECT
    DATE(
      TIMESTAMP_SECONDS(
        CASE WHEN order_state = 40 AND completed_time > 0 THEN completed_time
             ELSE accepted_time END
      ),
      'Asia/Bangkok'
    ) AS pay_date,
    CASE platform
      WHEN 'grab' THEN 'Grab'
      WHEN 'lineman' THEN 'LINE MAN'
      WHEN 'shopee' THEN 'Shopee'
      ELSE platform
    END AS method_name,
    '外卖平台' AS channel,
    COUNT(*) AS bill_cnt,
    ROUND(SUM(platform_total), 2) AS total_amount,
    CAST(0 AS NUMERIC) AS total_refund
  FROM `{project}.{dataset}.ttpos_takeout_order`
  WHERE delete_time = 0
    AND order_state IN (10, 20, 30, 40)
    AND platform IN ('grab', 'lineman', 'shopee')
    AND accepted_time > 0
    AND (
      CASE WHEN order_state = 40 AND completed_time > 0 THEN completed_time
           ELSE accepted_time END
    ) >= {start_ts}
    AND (
      CASE WHEN order_state = 40 AND completed_time > 0 THEN completed_time
           ELSE accepted_time END
    ) < {end_ts}
  GROUP BY pay_date, method_name
),
all_daily AS (
  SELECT * FROM pos_payment
  UNION ALL
  SELECT * FROM takeout_payment
)
SELECT
  IFNULL(
    (SELECT JSON_EXTRACT_SCALAR(`values`, '$.store_code')
     FROM `{project}.{dataset}.ttpos_setting`
     WHERE `key` = 'store' AND delete_time = 0 LIMIT 1), '') AS store_code,
  IFNULL(
    (SELECT JSON_EXTRACT_SCALAR(`values`, '$.store_name')
     FROM `{project}.{dataset}.ttpos_setting`
     WHERE `key` = 'store' AND delete_time = 0 LIMIT 1),
    (SELECT name FROM `{project}.{dataset}.ttpos_company` WHERE delete_time = 0 LIMIT 1)
  ) AS store_name,
  FORMAT_DATE('%Y-%m-%d', pay_date) AS pay_date,
  IFNULL(method_name, '未知') AS method_name,
  channel,
  bill_cnt,
  total_amount,
  total_refund,
  ROUND(total_amount - total_refund, 2) AS net_amount
FROM all_daily
WHERE bill_cnt > 0 OR total_amount > 0 OR total_refund > 0
ORDER BY pay_date, channel, total_amount DESC
"""


# ==================== 模板字典（便于查找） ====================

SQL_TEMPLATES = {
    # 销售业绩
    'sales_revenue': SALES_REVENUE_SQL,
    'takeout_summary': TAKEOUT_ORDER_SUMMARY_SQL,
    'comprehensive_sales': COMPREHENSIVE_SALES_SQL,

    # 物品消耗
    'material_consumption': MATERIAL_CONSUMPTION_SQL,
    'material_bom_consumption': MATERIAL_BOM_CONSUMPTION_SQL,
    'specific_material_consumption': SPECIFIC_MATERIAL_CONSUMPTION_SQL,

    # BOM商品
    'bom_product_sales': BOM_PRODUCT_SALES_SQL,

    # 日销量
    'daily_item_sales': DAILY_ITEM_SALES_SQL,

    # 库存
    'purchase_in': PURCHASE_IN_SQL,
    'material_related_sales': MATERIAL_RELATED_SALES_SQL,

    # 外卖营业额
    'takeout_revenue': TAKEOUT_REVENUE_SQL,

    # 订单国籍
    'order_nationality': ORDER_NATIONALITY_SQL,

    # 支付方式明细
    'payment_breakdown': PAYMENT_BREAKDOWN_SQL,
    'payment_breakdown_daily': PAYMENT_BREAKDOWN_DAILY_SQL,
}


def get_template(name: str) -> str:
    """获取指定名称的SQL模板"""
    if name not in SQL_TEMPLATES:
        raise ValueError(f"未知的SQL模板: {name}. 可用: {list(SQL_TEMPLATES.keys())}")
    return SQL_TEMPLATES[name]


def list_templates() -> list:
    """列出所有可用的SQL模板"""
    return list(SQL_TEMPLATES.keys())
