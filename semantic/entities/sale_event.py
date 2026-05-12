"""Sale event — 最细自然销售粒度 (item_uuid, price, channel) 上的聚合事实表。

为什么独立于 sale_line / takeout_line：
  - sale_line / takeout_line 在 item_uuid 粒度 GROUP BY，丢失了价格档信息
  - 这个 entity 多保留一阶维度 (price)，让上层报表可按任意 grain 子集再聚合
  - 同时附带 channel 标签（"dine" / "takeout"），未来按渠道展开零成本

报表层的使用方式：
  - profit_margin (现版) 不动 —— 继续走 sale_line / takeout_line，作为对账锚
  - profit_by_price (新) 直接消费 sale_event，按 (store, item, price) grain
  - 未来需求按任意维度展开 → 复用 sale_event + 新 grain 配置

代码重复说明：
  这个 CTE 跟 sale_line + takeout_line 业务逻辑等价，只是多 GROUP BY 一列。
  暂不去重——等 sale_event 通过校验器证明跟 sale_line 等价后，再统一底座。
  那次合并是 Phase 2 的事。
"""


def sale_event_cte() -> str:
    """Returns `sale_event AS (...)` body. Same占位符 ({project}/{dataset}/{start_ts}/{end_ts})
    as sale_line/takeout_line — drop-in compatible with engine.query()."""
    return """sale_event AS (
  -- 堂食按 (item, sale_price) 拆 —— 每个不同的价格档单独一行
  SELECT
    sp.product_package_uuid AS item_uuid,
    sp.product_sale_price AS price,
    'dine' AS channel,
    SUM(sp.product_num) AS qty,
    -- 营业额：标价 × 销量
    SUM(sp.product_sale_price * sp.product_num) AS sales_price,
    -- 标准金额：商品管理标价 × 销量
    SUM(IFNULL(pp.price, 0) * sp.product_num) AS original_amount,
    -- 实收金额：ttpos CountProductSale 真实口径（赠送归零、扣退款、用成交价）
    SUM(IF(sp.free_num > 0 OR sp.give_num > 0, 0,
           sp.product_final_price * (sp.product_num - sp.refund_num))) AS actual_amount,
    SUM(sp.refund_num) AS refund_qty,
    SUM(sp.product_sale_price * sp.refund_num) AS refund_amount,
    AVG(sp.member_order_discount_rate) AS avg_member_discount,
    SUM(sp.free_num) AS free_qty,
    SUM(sp.give_num) AS give_qty,
    -- 金额恒等式分项（跟 sale_line 同口径）
    SUM(IF(sp.free_num > 0, sp.product_sale_price * sp.product_num, 0)) AS free_amount,
    SUM(IF(sp.give_num > 0, sp.product_sale_price * sp.product_num, 0)) AS give_amount,
    SUM(IF(sp.free_num > 0 OR sp.give_num > 0, 0,
           (sp.product_sale_price - sp.product_final_price) * (sp.product_num - sp.refund_num))) AS discount_amount,
    0 AS cancelled_qty,
    0 AS cancelled_amount
  FROM `{project}`.`{dataset}`.`ttpos_statistics_product` sp
  LEFT JOIN `{project}`.`{dataset}`.`ttpos_product_package` pp
    ON pp.uuid = sp.product_package_uuid
  WHERE sp.complete_time >= {start_ts}
    AND sp.complete_time < {end_ts}
  GROUP BY item_uuid, price

  UNION ALL

  -- 外卖按 (item, price) 拆；state=60 取消单独算 cancelled_*
  SELECT
    toi.ttpos_product_package_uuid AS item_uuid,
    toi.price AS price,
    'takeout' AS channel,
    SUM(toi.quantity) AS qty,
    SUM(IF(t.order_state IN (10,20,30,40), toi.price * toi.quantity, 0)) AS sales_price,
    SUM(IF(t.order_state IN (10,20,30,40), IFNULL(pp.price, 0) * toi.quantity, 0)) AS original_amount,
    SUM(IF(t.order_state IN (10,20,30,40), toi.price * toi.quantity, 0)) AS actual_amount,
    0 AS refund_qty,
    0 AS refund_amount,
    1.0 AS avg_member_discount,
    0 AS free_qty,
    0 AS give_qty,
    -- 外卖无赠送/折扣概念，固定 0
    0 AS free_amount,
    0 AS give_amount,
    0 AS discount_amount,
    SUM(IF(t.order_state = 60, toi.quantity, 0)) AS cancelled_qty,
    SUM(IF(t.order_state = 60, toi.price * toi.quantity, 0)) AS cancelled_amount
  FROM `{project}`.`{dataset}`.`ttpos_takeout_order_item` toi
  JOIN `{project}`.`{dataset}`.`ttpos_takeout_order` t
    ON t.uuid = toi.takeout_order_uuid AND t.delete_time = 0
  LEFT JOIN `{project}`.`{dataset}`.`ttpos_product_package` pp
    ON pp.uuid = toi.ttpos_product_package_uuid
  WHERE toi.delete_time = 0
    AND toi.ttpos_product_package_uuid > 0
    AND t.order_state IN (10, 20, 30, 40, 60)
    AND t.accepted_time > 0
    AND (
      (t.order_state = 40 AND t.completed_time >= {start_ts} AND t.completed_time < {end_ts})
      OR (t.order_state != 40 AND t.accepted_time >= {start_ts} AND t.accepted_time < {end_ts})
    )
  GROUP BY item_uuid, price
)"""


# Metric column names produced by sale_event. Used by aggregate_by_grain to know
# which fields to SUM when collapsing to a coarser grain.
METRIC_COLUMNS = [
    "qty",
    "sales_price",
    "original_amount",
    "actual_amount",
    "refund_qty",
    "refund_amount",
    "free_qty",
    "give_qty",
    "free_amount",
    "give_amount",
    "discount_amount",
    "cancelled_qty",
    "cancelled_amount",
]

# Dimension columns naturally available on every sale_event row.
DIMENSION_COLUMNS = ["item_uuid", "price", "channel"]
