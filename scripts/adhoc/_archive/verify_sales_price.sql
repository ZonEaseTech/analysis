-- 验证脚本：比较 SQL 计算的 sales_price 与 API 的 sales_price
-- 执行方式：在 BQ 控制台运行，对比 API 返回的 22号店数据

DECLARE start_ts INT64 DEFAULT 1740700800;  -- 2026-03-01
DECLARE end_ts INT64 DEFAULT 1743379200;    -- 2026-04-01

-- 堂食成交价
WITH dine_sales_price AS (
  SELECT
    sop.product_package_uuid AS item_uuid,
    SUM(sop.num * sop.copy_num * IF(sop.unit_num = 0, 1, sop.unit_num)) AS qty,
    SUM(sop.total_price * sop.num * sop.copy_num * IF(sop.unit_num = 0, 1, sop.unit_num)) AS sales_price
  FROM `ttpos-255512.prod_ttpos_prod_20241220.ttpos_sale_order_product` sop
  JOIN `ttpos-255512.prod_ttpos_prod_20241220.ttpos_sale_bill` sb
    ON sb.uuid = sop.sale_bill_uuid AND sb.delete_time = 0
  WHERE sop.delete_time = 0
    AND sop.cancel_time = 0
    AND sb.status = 1
    AND sb.finish_time >= start_ts
    AND sb.finish_time < end_ts
    AND sop.product_type IN (0, 2)
  GROUP BY sop.product_package_uuid
),

-- 外卖成交价
takeout_sales_price AS (
  SELECT
    toi.ttpos_product_package_uuid AS item_uuid,
    SUM(toi.quantity) AS qty,
    SUM(IF(t.order_state IN (10,20,30,40), toi.price * toi.quantity, 0)) AS sales_price
  FROM `ttpos-255512.prod_ttpos_prod_20241220.ttpos_takeout_order_item` toi
  JOIN `ttpos-255512.prod_ttpos_prod_20241220.ttpos_takeout_order` t
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

merged AS (
  SELECT
    COALESCE(d.item_uuid, t.item_uuid) AS item_uuid,
    IFNULL(d.qty, 0) + IFNULL(t.qty, 0) AS qty,
    IFNULL(d.sales_price, 0) + IFNULL(t.sales_price, 0) AS sales_price
  FROM dine_sales_price d
  FULL OUTER JOIN takeout_sales_price t USING (item_uuid)
)

-- 验证 22号店（shop_uuid = 2947521978368000）
-- 注意：需要关联 shop 过滤，当前 SQL 是所有门店的数据
SELECT 
  SUM(sales_price) AS total_sales_price
FROM merged
