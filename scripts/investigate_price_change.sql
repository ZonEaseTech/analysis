-- 001号店「超值套餐5」订单回溯查询
-- 用途：确认价格变动原因（促销 vs 涨价）

-- ============================================
-- 1. 基础订单统计（statistics_product 聚合）
-- ============================================
DECLARE start_ts INT64 DEFAULT 1772298000;  -- 2026-03-01 00:00:00+07:00
DECLARE end_ts INT64 DEFAULT 1774976400;    -- 2026-04-01 00:00:00+07:00
DECLARE shop_uuid INT64 DEFAULT 1958987436032000;  -- 001号店
DECLARE product_uuid INT64 DEFAULT 3701534182998018;  -- 超值套餐5

-- 查看每日价格分布
SELECT
  EXTRACT(DATE FROM TIMESTAMP_SECONDS(complete_time)) AS sale_date,
  product_sale_price,
  COUNT(*) AS order_count,
  SUM(product_num) AS total_qty,
  SUM(product_sale_price * product_num) AS daily_amount
FROM `diyl-407103.shop1958987436032000.ttpos_statistics_product`
WHERE complete_time >= start_ts
  AND complete_time < end_ts
  AND shop_uuid = shop_uuid
  AND product_package_uuid = product_uuid
GROUP BY 1, 2
ORDER BY 1, 2;

-- ============================================
-- 2. 原始订单明细（sale_order_product + sale_bill）
-- ============================================
SELECT
  sb.order_no,
  sb.serial_no,
  sop.num,
  sop.price AS order_price,
  sop.total_price AS unit_price,
  sop.num * sop.total_price AS line_amount,
  sb.finish_time,
  TIMESTAMP_SECONDS(sb.finish_time) AS finish_dt,
  sb.status,
  sb.payment_amount AS bill_payment,
  sb.amount AS bill_amount
FROM `diyl-407103.shop1958987436032000.ttpos_sale_order_product` sop
JOIN `diyl-407103.shop1958987436032000.ttpos_sale_bill` sb
  ON sb.uuid = sop.sale_bill_uuid AND sb.delete_time = 0
WHERE sop.delete_time = 0
  AND sop.cancel_time = 0
  AND sb.status = 1
  AND sb.finish_time >= start_ts
  AND sb.finish_time < end_ts
  AND sop.product_package_uuid = product_uuid
ORDER BY sb.finish_time;

-- ============================================
-- 3. 商品价格变动历史（如有 product_price_history 表）
-- ============================================
-- 注意：ttpos 通常没有价格历史表，价格变动通过订单价格反推

-- 反推价格变动时间点的查询
SELECT
  EXTRACT(DATE FROM TIMESTAMP_SECONDS(complete_time)) AS sale_date,
  MIN(product_sale_price) AS min_price,
  MAX(product_sale_price) AS max_price,
  AVG(product_sale_price) AS avg_price,
  COUNT(DISTINCT product_sale_price) AS price_variants
FROM `diyl-407103.shop1958987436032000.ttpos_statistics_product`
WHERE complete_time >= start_ts
  AND complete_time < end_ts
  AND shop_uuid = shop_uuid
  AND product_package_uuid = product_uuid
GROUP BY 1
ORDER BY 1;

-- ============================================
-- 4. 检查是否有促销/活动标记
-- ============================================
-- 查看订单是否有 discount 相关字段
SELECT
  sp.product_sale_price,
  sp.product_final_price,
  sp.member_order_discount_rate,
  sp.tax_rate,
  sp.tax_fee,
  sp.service_fee,
  sp.free_num,
  sp.give_num,
  COUNT(*) AS cnt
FROM `diyl-407103.shop1958987436032000.ttpos_statistics_product` sp
WHERE sp.complete_time >= start_ts
  AND sp.complete_time < end_ts
  AND sp.shop_uuid = shop_uuid
  AND sp.product_package_uuid = product_uuid
GROUP BY 1,2,3,4,5,6,7,8
ORDER BY 1;

-- ============================================
-- 5. 对比其他门店同期价格（确认是全局涨价还是单店促销）
-- ============================================
SELECT
  sp.shop_uuid,
  s.name AS shop_name,
  AVG(sp.product_sale_price) AS avg_price,
  MIN(sp.product_sale_price) AS min_price,
  MAX(sp.product_sale_price) AS max_price,
  COUNT(*) AS order_count
FROM `diyl-407103.shop1958987436032000.ttpos_statistics_product` sp
LEFT JOIN `diyl-407103.shop1958987436032000.ttpos_shop` s 
  ON s.uuid = sp.shop_uuid
WHERE sp.complete_time >= start_ts
  AND sp.complete_time < end_ts
  AND sp.product_package_uuid = product_uuid
GROUP BY 1, 2
ORDER BY 3;
