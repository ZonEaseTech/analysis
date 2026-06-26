-- 成交价（sales_price）SQL 计算模板
-- 
-- 用途：计算商品级别的成交价总额（对应 API 的 sales_price）
-- 与实收金额（total_pay_price）的区别：
--   - 成交价 = 商品成交价 × 销量（扣除取消/退订前的原始成交）
--   - 实收金额 = 实际到账金额（扣除平台佣金、退订等）
--
-- 堂食数据源：ttpos_sale_order_product + ttpos_sale_bill
-- 外卖数据源：ttpos_takeout_order_item + ttpos_takeout_order
--
-- 使用方式：
--   1. 在 BigQuery 控制台执行
--   2. 对比 API 返回的 sales_price 验证结果
--   3. 确认一致后，集成到利润报表中

DECLARE start_ts INT64 DEFAULT 1740700800;  -- 2026-03-01 00:00:00
DECLARE end_ts INT64 DEFAULT 1743379200;    -- 2026-04-01 00:00:00
DECLARE shop_uuid STRING DEFAULT '2947521978368000';  -- 22号店

-- 堂食成交价（从订单明细计算）
WITH dine_sales_price AS (
  SELECT
    sop.product_package_uuid AS item_uuid,
    SUM(sop.num * sop.copy_num * IF(sop.unit_num = 0, 1, sop.unit_num)) AS qty,
    SUM(sop.total_price * sop.num * sop.copy_num * IF(sop.unit_num = 0, 1, sop.unit_num)) AS sales_price
  FROM `diyl-407103.shop2947521978368000.ttpos_sale_order_product` sop
  JOIN `diyl-407103.shop2947521978368000.ttpos_sale_bill` sb
    ON sb.uuid = sop.sale_bill_uuid AND sb.delete_time = 0
  WHERE sop.delete_time = 0
    AND sop.cancel_time = 0        -- 排除取消的订单
    AND sb.status = 1              -- 已完成
    AND sb.finish_time >= start_ts
    AND sb.finish_time < end_ts
    AND sop.product_type IN (0, 2)  -- 0=单品, 2=套餐
  GROUP BY sop.product_package_uuid
),

-- 外卖成交价（从订单明细计算）
takeout_sales_price AS (
  SELECT
    toi.ttpos_product_package_uuid AS item_uuid,
    SUM(toi.quantity) AS qty,
    SUM(IF(t.order_state IN (10,20,30,40), 
           toi.price * toi.quantity, 
           0)) AS sales_price
  FROM `diyl-407103.shop2947521978368000.ttpos_takeout_order_item` toi
  JOIN `diyl-407103.shop2947521978368000.ttpos_takeout_order` t
    ON t.uuid = toi.takeout_order_uuid AND t.delete_time = 0
  WHERE toi.delete_time = 0
    AND toi.ttpos_product_package_uuid > 0
    AND t.order_state IN (10, 20, 30, 40, 60)
    AND t.accepted_time > 0
    AND (
      (t.order_state = 40 AND t.completed_time >= start_ts AND t.completed_time < end_ts)
      OR (t.order_state != 40 AND t.accepted_time >= start_ts AND t.accepted_time < end_ts)
    )
  GROUP BY toi.ttpos_product_package_uuid
),

-- 合并堂食和外卖
merged_sales_price AS (
  SELECT
    COALESCE(d.item_uuid, t.item_uuid) AS item_uuid,
    IFNULL(d.qty, 0) + IFNULL(t.qty, 0) AS qty,
    IFNULL(d.sales_price, 0) + IFNULL(t.sales_price, 0) AS sales_price
  FROM dine_sales_price d
  FULL OUTER JOIN takeout_sales_price t USING (item_uuid)
)

-- 关联商品信息，输出结果
SELECT
  m.item_uuid,
  REGEXP_REPLACE(COALESCE(
    JSON_EXTRACT_SCALAR(pp.name, '$.zh'),
    JSON_EXTRACT_SCALAR(pp.name, '$.en'),
    '未知'
  ), r'^\\s+|\\s+$', '') AS item_name,
  m.qty,
  m.sales_price
FROM merged_sales_price m
JOIN `diyl-407103.shop2947521978368000.ttpos_product_package` pp
  ON pp.uuid = m.item_uuid
WHERE m.qty > 0
ORDER BY m.sales_price DESC
