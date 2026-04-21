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

# 综合销售业绩（POS + 外卖平台）
COMPREHENSIVE_SALES_SQL = """
WITH 
pos_sales AS (
  SELECT 
    ROUND(SUM(amount), 2) AS turnover,
    ROUND(SUM(payment_amount), 2) AS received,
    COUNT(*) AS cnt
  FROM `{project}.{dataset}.ttpos_sale_bill`
  WHERE delete_time = 0 AND status = 1
    AND finish_time >= {start_ts} AND finish_time < {end_ts}
),
takeout_sales AS (
  SELECT 
    ROUND(SUM(subtotal), 2) AS turnover,
    ROUND(SUM(platform_total), 2) AS received,
    COUNT(*) AS cnt
  FROM `{project}.{dataset}.ttpos_takeout_order`
  WHERE delete_time = 0 AND order_state = 40
    AND platform IN ('grab', 'lineman', 'shopee')
    AND completed_time >= {start_ts} AND completed_time < {end_ts}
)
SELECT
  c.name AS store_name,
  IFNULL(ps.turnover, 0) + IFNULL(ts.turnover, 0) AS total_turnover,
  IFNULL(ps.received, 0) + IFNULL(ts.received, 0) AS total_received,
  IFNULL(ps.cnt, 0) + IFNULL(ts.cnt, 0) AS total_orders
FROM pos_sales ps
CROSS JOIN takeout_sales ts
CROSS JOIN `{project}.{dataset}.ttpos_company` c
WHERE c.delete_time = 0
LIMIT 1
"""


# ==================== 2. 物品消耗相关 ====================

# 物品消耗统计（sale_order_material + takeout_order_material）
MATERIAL_CONSUMPTION_SQL = """
WITH 
pos_consumption AS (
  SELECT 
    m.uuid AS material_uuid,
    JSON_EXTRACT_SCALAR(m.name, '$.zh') AS name_zh,
    JSON_EXTRACT_SCALAR(m.name, '$.en') AS name_en,
    JSON_EXTRACT_SCALAR(m.name, '$.th') AS name_th,
    SUM(som.num) AS num,
    JSON_EXTRACT_SCALAR(pu.name, '$.zh') AS unit_name
  FROM `{project}.{dataset}.ttpos_sale_order_material` som
  JOIN `{project}.{dataset}.ttpos_material` m 
    ON m.uuid = som.material_uuid AND m.delete_time = 0
  JOIN `{project}.{dataset}.ttpos_sale_bill` sb 
    ON sb.uuid = som.sale_bill_uuid AND sb.delete_time = 0
  LEFT JOIN `{project}.{dataset}.ttpos_material_unit` mu 
    ON mu.material_uuid = m.uuid AND mu.is_default = 1 AND mu.delete_time = 0
  LEFT JOIN `{project}.{dataset}.ttpos_product_unit` pu 
    ON pu.uuid = mu.unit_uuid AND pu.delete_time = 0
  WHERE som.delete_time = 0
    AND sb.status = 1
    AND sb.finish_time >= {start_ts} 
    AND sb.finish_time < {end_ts}
  GROUP BY m.uuid, m.name, pu.name
),
takeout_consumption AS (
  SELECT 
    m.uuid AS material_uuid,
    SUM(tom.num) AS num
  FROM `{project}.{dataset}.ttpos_takeout_order_material` tom
  JOIN `{project}.{dataset}.ttpos_material` m 
    ON m.uuid = tom.material_uuid AND m.delete_time = 0
  JOIN `{project}.{dataset}.ttpos_takeout_order` tko 
    ON tko.uuid = tom.takeout_order_uuid AND tko.delete_time = 0
  WHERE tom.delete_time = 0
    AND tko.order_state = 40
    AND tko.completed_time > 0
    AND tko.completed_time >= {start_ts} 
    AND tko.completed_time < {end_ts}
  GROUP BY m.uuid
)
SELECT 
  pc.material_uuid,
  COALESCE(pc.name_zh, pc.name_en, pc.name_th, '未知') AS material_name,
  ROUND(pc.num + IFNULL(tc.num, 0), 2) AS total_num,
  pc.unit_name
FROM pos_consumption pc
LEFT JOIN takeout_consumption tc ON tc.material_uuid = pc.material_uuid
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

# BOM商品销量统计（区分已设BOM和未设BOM）
BOM_PRODUCT_SALES_SQL = """
WITH 
product_sales AS (
  SELECT
    sop.product_package_uuid,
    JSON_EXTRACT_SCALAR(pp.name, '$.zh') AS product_name_zh,
    JSON_EXTRACT_SCALAR(pp.name, '$.en') AS product_name_en,
    JSON_EXTRACT_SCALAR(pp.name, '$.th') AS product_name_th,
    CASE
      WHEN sop.product_type = 0 THEN sop.num
      WHEN sop.product_type = 2 THEN sop.num * sop.copy_num * IF(sop.unit_num = 0, 1, sop.unit_num)
      ELSE 0
    END AS qty,
    IF(bom_set.pp_uuid IS NOT NULL, 1, 0) AS has_bom
  FROM `{project}.{dataset}.ttpos_sale_order_product` sop
  JOIN `{project}.{dataset}.ttpos_sale_bill` sb 
    ON sb.uuid = sop.sale_bill_uuid AND sb.delete_time = 0
  JOIN `{project}.{dataset}.ttpos_product_package` pp 
    ON pp.uuid = sop.product_package_uuid AND pp.delete_time = 0
  LEFT JOIN (
    SELECT DISTINCT pb.product_package_uuid AS pp_uuid
    FROM `{project}.{dataset}.ttpos_product_bom` pb
    WHERE pb.delete_time = 0
      AND (pb.product_flavor_uuid > 0 OR pb.product_sauce_uuid > 0)
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
  ) bom_set ON bom_set.pp_uuid = sop.product_package_uuid
  WHERE sop.delete_time = 0
    AND sop.cancel_time = 0
    AND sb.status = 1
    AND sb.finish_time >= {start_ts}
    AND sb.finish_time < {end_ts}
    AND sop.product_type IN (0, 2)
)
SELECT 
  product_package_uuid,
  COALESCE(product_name_zh, product_name_en, product_name_th, '未知') AS product_name,
  ROUND(SUM(qty), 2) AS total_qty,
  has_bom
FROM product_sales
GROUP BY product_package_uuid, product_name_zh, product_name_en, product_name_th, has_bom
HAVING total_qty > 0
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
    toi.product_package_uuid,
    JSON_EXTRACT_SCALAR(pp.name, '$.zh') AS product_name_zh,
    JSON_EXTRACT_SCALAR(pp.name, '$.en') AS product_name_en,
    SUM(toi.quantity) AS qty
  FROM `{project}.{dataset}.ttpos_takeout_order_item` toi
  JOIN `{project}.{dataset}.ttpos_takeout_order` tko 
    ON tko.uuid = toi.takeout_order_uuid AND tko.delete_time = 0
  LEFT JOIN `{project}.{dataset}.ttpos_product_package` pp 
    ON pp.uuid = toi.product_package_uuid AND pp.delete_time = 0
  WHERE toi.delete_time = 0
    AND tko.order_state IN (10, 20, 30, 40, 60)
    AND (
      (tko.order_state = 40 AND tko.completed_time > 0 
       AND tko.completed_time >= {start_ts} AND tko.completed_time < {end_ts})
      OR
      (tko.order_state != 40 AND tko.accepted_time >= {start_ts} 
       AND tko.accepted_time < {end_ts})
    )
  GROUP BY sale_date, toi.product_package_uuid, pp.name
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

# 外卖营业额统计（POS侧 + 外卖平台侧）
# 外卖订单判定（满足任一即计入，同一账单只算一次）：
#   条件1：该账单下任一销售订单存在已支付记录，且 payment_method_name 去空格、小写后匹配 robinhood|grab|lineman|shopee
#   条件2：order_source_uuid>0 且（订单来源多语言名 或 order_source_name JSON 快照）含 Grab / LINE MAN（lineman、line man 等）
#   条件3：bill_type=2（会员外送）或 order_source_uuid>0（外卖渠道）或 dining_method=1（打包/外带）
TAKEOUT_REVENUE_SQL = """
WITH finished_bills AS (
  SELECT
    sb.uuid AS bill_uuid,
    sb.amount AS bill_amount,
    sb.payment_amount AS bill_payment_amount,
    sb.bill_type,
    sb.dining_method,
    sb.order_source_uuid,
    sb.order_source_name
  FROM `{project}`.`{dataset}`.`ttpos_sale_bill` AS sb
  WHERE sb.delete_time = 0
    AND sb.status = 1
    AND sb.finish_time >= {start_ts}
    AND sb.finish_time < {end_ts}
),

bill_takeout AS (
  SELECT
    fb.bill_uuid,
    (
      -- 条件1：支付方式
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
            LOWER(REPLACE(po.payment_method_name, ' ', '')),
            r'robinhood|grab|lineman|shopee'
          )
      )
      -- 条件2：Grab / LINE MAN 渠道（名称匹配，不区分大小写；空格已压缩）
      OR (
        fb.order_source_uuid > 0
        AND (
          EXISTS (
            SELECT 1
            FROM `{project}`.`{dataset}`.`ttpos_order_source` AS os
            LEFT JOIN `{project}`.`{dataset}`.`ttpos_multi_language_name` AS mln
              ON mln.uuid = os.multi_language_name_uuid
              AND mln.delete_time = 0
            WHERE os.uuid = fb.order_source_uuid
              AND os.delete_time = 0
              AND (
                REGEXP_CONTAINS(
                  LOWER(REPLACE(CONCAT(
                    IFNULL(mln.zh_name, ''),
                    IFNULL(mln.th_name, ''),
                    IFNULL(mln.en_name, '')
                  ), ' ', '')),
                  r'grab|lineman'
                )
              )
          )
          OR REGEXP_CONTAINS(
            LOWER(REPLACE(CONCAT(
              IFNULL(JSON_EXTRACT_SCALAR(fb.order_source_name, '$.zh'), ''),
              IFNULL(JSON_EXTRACT_SCALAR(fb.order_source_name, '$.th'), ''),
              IFNULL(JSON_EXTRACT_SCALAR(fb.order_source_name, '$.en'), '')
            ), ' ', '')),
            r'grab|lineman'
          )
        )
      )
      -- 条件3：外卖/外送/打包
      OR fb.bill_type = 2
      OR fb.order_source_uuid > 0
      OR fb.dining_method = 1
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
ORDER BY sb.finish_time DESC
"""


# ==================== 模板字典（便于查找） ====================

SQL_TEMPLATES = {
    # 销售业绩
    'sales_revenue': SALES_REVENUE_SQL,
    'takeout_summary': TAKEOUT_ORDER_SUMMARY_SQL,
    'comprehensive_sales': COMPREHENSIVE_SALES_SQL,

    # 物品消耗
    'material_consumption': MATERIAL_CONSUMPTION_SQL,
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
}


def get_template(name: str) -> str:
    """获取指定名称的SQL模板"""
    if name not in SQL_TEMPLATES:
        raise ValueError(f"未知的SQL模板: {name}. 可用: {list(SQL_TEMPLATES.keys())}")
    return SQL_TEMPLATES[name]


def list_templates() -> list:
    """列出所有可用的SQL模板"""
    return list(SQL_TEMPLATES.keys())
