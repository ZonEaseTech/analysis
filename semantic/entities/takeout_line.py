"""Takeout sales entity — per-item aggregation from ttpos_takeout_order_item.

Owns the **ttpos RankTakeoutProduct** semantic:
  - Dynamic time condition: state=40 uses completed_time, others use accepted_time
  - Revenue counts states (10, 20, 30, 40); state=60 cancellations contribute 0
    to sales_price/actual_amount but ARE captured separately as cancelled_qty
    / cancelled_amount so the report can audit them.
Sourced from ttpos-server-go/main/app/repository/statistics_takeout.go:451-502.

Returned fields mirror shop_sales for downstream FULL OUTER JOIN compatibility.
Takeout has no native free/give/refund concept, so those fields are zeroed;
avg_member_discount defaults to 1.0 (no discount).

金额单位: 萨当 (satang, INT64) — 唯一舍入点在本 CTE 输出层 (spec §6 B).
"""

from semantic.dimensions.test_business import takeout_test_business_clause


def takeout_sales_cte(exclude_test_business: bool = False) -> str:
    """Returns `takeout_sales AS (...)` body with placeholders intact.

    exclude_test_business=True 时加 NOT EXISTS 排除 takeout_order.create_time 落在
    ttpos_business_status_period 内的记录 (对齐 ttpos 后台口径)。
    """
    tb_clause = ("\n    " + takeout_test_business_clause("t")) if exclude_test_business else ""
    return """takeout_sales AS (
  -- ttpos 源码: ttpos-server-go/main/app/repository/statistics_takeout.go:451-502 (RankTakeoutProduct)
  -- 时间过滤是 dynamic time condition: state=40 用 completed_time, 其他用 accepted_time
  -- 营业额只算 state IN (10,20,30,40)，state=60 取消订单计 0
  SELECT
    toi.ttpos_product_package_uuid AS item_uuid,
    -- 销量：含 state=60 取消订单（跟 ttpos 后台口径一致；取消单独列出）
    SUM(toi.quantity) AS qty,
    -- 营业额 / 实收：state IN (10,20,30,40) 算，state=60 取消订单计 0
    -- 外卖没有 free/give/refund 概念，actual_amount = sales_price (萨当整数化, 唯一舍入点)
    CAST(ROUND(SUM(IF(t.order_state IN (10,20,30,40), toi.price * toi.quantity, 0)) * 100) AS INT64) AS sales_price,
    -- 毛额: 不分 state 全量. GROSS_AMOUNT 恒等式据此审计 state 枚举完备性 —
    -- 若 ttpos 新增 state, 金额从 sales_price/cancelled 之间漏掉, 恒等式立刻 fire
    CAST(ROUND(SUM(toi.price * toi.quantity) * 100) AS INT64) AS gross_amount,
    CAST(ROUND(SUM(IF(t.order_state IN (10,20,30,40), toi.price * toi.quantity, 0)) * 100) AS INT64) AS actual_amount,
    CAST(ROUND(SUM(IF(t.order_state IN (10,20,30,40), IFNULL(pp.price, 0) * toi.quantity, 0)) * 100) AS INT64) AS original_amount,
    CAST(0 AS INT64) AS refund_qty,
    CAST(0 AS INT64) AS refund_amount,
    1.0 AS avg_member_discount,
    CAST(0 AS INT64) AS free_qty,
    CAST(0 AS INT64) AS give_qty,
    -- 外卖无赠送/折扣概念，3 个金额项固定 0（金额恒等式用，保证 schema 跟 shop_sales 对齐）
    CAST(0 AS INT64) AS free_amount,
    CAST(0 AS INT64) AS give_amount,
    CAST(0 AS INT64) AS discount_amount,
    -- 取消订单：state=60 单独统计
    SUM(IF(t.order_state = 60, toi.quantity, 0)) AS cancelled_qty,
    CAST(ROUND(SUM(IF(t.order_state = 60, toi.price * toi.quantity, 0)) * 100) AS INT64) AS cancelled_amount
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
    )""" + tb_clause + """
  GROUP BY item_uuid
)"""
