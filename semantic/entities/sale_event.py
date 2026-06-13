"""Sale event — 最细自然销售粒度 (item_uuid, price, channel) 上的聚合事实表。

为什么独立于 sale_line / takeout_line：
  - sale_line / takeout_line 在 item_uuid 粒度 GROUP BY，丢失了价格档信息
  - 这个 entity 多保留一阶维度 (price)，让上层报表可按任意 grain 子集再聚合
  - 同时附带 channel 标签（"dine" / "takeout"），未来按渠道展开零成本

金额单位: 萨当 (satang, INT64) — 唯一舍入点在本 CTE 输出层 (spec §6 B).
ttpos 源金额是 decimal(12,2) 元; 这里 CAST(ROUND(SUM(...)*100) AS INT64) 一次性
整数化, 下游加法精确零误差. 估算域 (物料单价/费率/COGS/利润/比率) 仍 float.

报表层的使用方式：
  - profit_margin (现版) 不动 —— 继续走 sale_line / takeout_line，作为对账锚
  - profit_by_price (新) 直接消费 sale_event，按 (store, item, price) grain
  - 未来需求按任意维度展开 → 复用 sale_event + 新 grain 配置

代码重复说明：
  这个 CTE 跟 sale_line + takeout_line 业务逻辑等价，只是多 GROUP BY 一列。
  暂不去重——等 sale_event 通过校验器证明跟 sale_line 等价后，再统一底座。
  那次合并是 Phase 2 的事。
"""


from semantic.dimensions.test_business import (
    dine_test_business_clause,
    takeout_test_business_clause,
)


def sale_event_cte(dine_excludes=None, takeout_excludes=None,
                   exclude_test_business: bool = False,
                   with_business_date: bool = False) -> str:
    """Returns `sale_event AS (...)` body. Same占位符 ({project}/{dataset}/{start_ts}/{end_ts})
    as sale_line/takeout_line — drop-in compatible with engine.query().

    dine_excludes: set[int] of ttpos_statistics_product.sale_order_uuid to drop
    takeout_excludes: set[int] of ttpos_takeout_order.uuid to drop
    exclude_test_business: True 时对齐 ttpos 后台口径排除「测试营业时段」内的订单。
    with_business_date: True 时多加一列 business_date (DATE, BKK 时区) 并加入 GROUP BY,
        让上层报表能按日聚合 (e.g. 每日趋势 / 环比). 默认 False 保持向后兼容.
    """
    bd_select_dine = (
        "    DATE(TIMESTAMP_SECONDS(sp.complete_time), 'Asia/Bangkok') AS business_date,\n"
        if with_business_date else ""
    )
    bd_select_takeout = (
        "    DATE(TIMESTAMP_SECONDS(IF(t.order_state = 40, t.completed_time, t.accepted_time)),"
        " 'Asia/Bangkok') AS business_date,\n"
        if with_business_date else ""
    )
    bd_group = ", business_date" if with_business_date else ""
    def _not_in(field: str, ids) -> str:
        if not ids:
            return ""
        return f"    AND {field} NOT IN ({', '.join(str(int(x)) for x in ids)})\n"
    dine_filter = _not_in("sp.sale_order_uuid", dine_excludes)
    takeout_filter = _not_in("t.uuid", takeout_excludes)
    sb_join = ("""LEFT JOIN `{project}`.`{dataset}`.`ttpos_sale_bill` sb
    ON sb.uuid = sp.sale_bill_uuid AND sb.delete_time = 0
  """ if exclude_test_business else "")
    dine_tb = ("    " + dine_test_business_clause("sb") + "\n") if exclude_test_business else ""
    takeout_tb = ("    " + takeout_test_business_clause("t") + "\n") if exclude_test_business else ""
    return ("""sale_event AS (
  -- 堂食按 (item, sale_price) 拆 —— 每个不同的价格档单独一行
  SELECT
    sp.product_package_uuid AS item_uuid,
    sp.product_sale_price AS price,
    'dine' AS channel,
    'pos' AS sub_channel,
""" + bd_select_dine + """    SUM(sp.product_num) AS qty,
    -- 营业额：标价 × 销量 (萨当整数化, 唯一舍入点)
    CAST(ROUND(SUM(sp.product_sale_price * sp.product_num) * 100) AS INT64) AS sales_price,
    -- 毛额 (守恒闭环锚): 堂食无 state, 与 sales_price 同式
    CAST(ROUND(SUM(sp.product_sale_price * sp.product_num) * 100) AS INT64) AS gross_amount,
    -- 标准金额：商品管理标价 × 销量
    CAST(ROUND(SUM(IFNULL(pp.price, 0) * sp.product_num) * 100) AS INT64) AS original_amount,
    -- 实收金额：ttpos CountProductSale 真实口径（赠送归零、扣退款、用成交价）
    CAST(ROUND(SUM(IF(sp.free_num > 0 OR sp.give_num > 0, 0,
           sp.product_final_price * (sp.product_num - sp.refund_num))) * 100) AS INT64) AS actual_amount,
    SUM(sp.refund_num) AS refund_qty,
    CAST(ROUND(SUM(sp.product_sale_price * sp.refund_num) * 100) AS INT64) AS refund_amount,
    AVG(sp.member_order_discount_rate) AS avg_member_discount,
    SUM(sp.free_num) AS free_qty,
    SUM(sp.give_num) AS give_qty,
    -- 金额恒等式分项（跟 sale_line 同口径）
    CAST(ROUND(SUM(IF(sp.free_num > 0, sp.product_sale_price * sp.product_num, 0)) * 100) AS INT64) AS free_amount,
    CAST(ROUND(SUM(IF(sp.give_num > 0, sp.product_sale_price * sp.product_num, 0)) * 100) AS INT64) AS give_amount,
    CAST(ROUND(SUM(IF(sp.free_num > 0 OR sp.give_num > 0, 0,
           (sp.product_sale_price - sp.product_final_price) * (sp.product_num - sp.refund_num))) * 100) AS INT64) AS discount_amount,
    CAST(0 AS INT64) AS cancelled_qty,
    CAST(0 AS INT64) AS cancelled_amount
  FROM `{project}`.`{dataset}`.`ttpos_statistics_product` sp
  """ + sb_join + """LEFT JOIN (
    SELECT uuid, ANY_VALUE(price) AS price
    FROM `{project}`.`{dataset}`.`ttpos_product_package`
    GROUP BY uuid
  ) pp
    ON pp.uuid = sp.product_package_uuid
  WHERE sp.complete_time >= {start_ts}
    AND sp.complete_time < {end_ts}
""" + dine_tb + dine_filter + """  GROUP BY item_uuid, price, sub_channel""" + bd_group + """

  UNION ALL

  -- 外卖按 (item, price) 拆；state=60 取消单独算 cancelled_*
  SELECT
    toi.ttpos_product_package_uuid AS item_uuid,
    toi.price AS price,
    'takeout' AS channel,
    -- 外卖子渠道：grab/lineman/shopee/foodpanda/... 或 POS 本地下单 → 'pos_takeout'
    IFNULL(NULLIF(t.platform, ''), 'pos_takeout') AS sub_channel,
""" + bd_select_takeout + """    SUM(toi.quantity) AS qty,
    CAST(ROUND(SUM(IF(t.order_state IN (10,20,30,40), toi.price * toi.quantity, 0)) * 100) AS INT64) AS sales_price,
    -- 毛额: 不分 state 全量. GROSS_AMOUNT 恒等式据此审计 state 枚举完备性 —
    -- 若 ttpos 新增 state, 金额从 sales_price/cancelled 之间漏掉, 恒等式立刻 fire
    CAST(ROUND(SUM(toi.price * toi.quantity) * 100) AS INT64) AS gross_amount,
    CAST(ROUND(SUM(IF(t.order_state IN (10,20,30,40), IFNULL(pp.price, 0) * toi.quantity, 0)) * 100) AS INT64) AS original_amount,
    CAST(ROUND(SUM(IF(t.order_state IN (10,20,30,40), toi.price * toi.quantity, 0)) * 100) AS INT64) AS actual_amount,
    CAST(0 AS INT64) AS refund_qty,
    CAST(0 AS INT64) AS refund_amount,
    1.0 AS avg_member_discount,
    CAST(0 AS INT64) AS free_qty,
    CAST(0 AS INT64) AS give_qty,
    -- 外卖无赠送/折扣概念，固定 0
    CAST(0 AS INT64) AS free_amount,
    CAST(0 AS INT64) AS give_amount,
    CAST(0 AS INT64) AS discount_amount,
    SUM(IF(t.order_state = 60, toi.quantity, 0)) AS cancelled_qty,
    CAST(ROUND(SUM(IF(t.order_state = 60, toi.price * toi.quantity, 0)) * 100) AS INT64) AS cancelled_amount
  FROM `{project}`.`{dataset}`.`ttpos_takeout_order_item` toi
  JOIN `{project}`.`{dataset}`.`ttpos_takeout_order` t
    ON t.uuid = toi.takeout_order_uuid AND t.delete_time = 0
  LEFT JOIN (
    SELECT uuid, ANY_VALUE(price) AS price
    FROM `{project}`.`{dataset}`.`ttpos_product_package`
    GROUP BY uuid
  ) pp
    ON pp.uuid = toi.ttpos_product_package_uuid
  WHERE toi.delete_time = 0
    AND toi.ttpos_product_package_uuid > 0
    AND t.order_state IN (10, 20, 30, 40, 60)
    AND t.accepted_time > 0
    AND (
      (t.order_state = 40 AND t.completed_time >= {start_ts} AND t.completed_time < {end_ts})
      OR (t.order_state != 40 AND t.accepted_time >= {start_ts} AND t.accepted_time < {end_ts})
    )
""" + takeout_tb + takeout_filter + """  GROUP BY item_uuid, price, sub_channel""" + bd_group + """
)""")


# Metric column names produced by sale_event. Used by aggregate_by_grain to know
# which fields to SUM when collapsing to a coarser grain.
METRIC_COLUMNS = [
    "qty",
    "sales_price",
    "gross_amount",
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
DIMENSION_COLUMNS = ["item_uuid", "price", "channel", "sub_channel"]
