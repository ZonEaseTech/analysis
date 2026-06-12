"""Price-tier breakdown — top-3 selling prices per item + 'other' bucket.

Sources the same time-windowed dine + takeout rows used by `sale_line` and
`takeout_line`, but at row granularity (no aggregation by item alone).
The takeout half **excludes** state=60 cancellations so that
  Σ(price_k × qty_k) + other_qty × avg_price
reconciles to the `sales_price` column on the merged shop+takeout row.

Returns 4 chained CTEs comma-separated (no trailing comma):
  price_breakdown_raw  ← UNION ALL of dine + takeout rows
  price_breakdown      ← grouped by (item_uuid, price)
  price_ranked         ← ROW_NUMBER over qty DESC
  price_top3           ← pivots ranks 1-3 + sums rank>3 into other_qty
"""

from semantic.dimensions.test_business import (
    dine_test_business_clause,
    takeout_test_business_clause,
)


def price_top3_ctes(exclude_test_business: bool = False) -> str:
    """Returns 4 CTEs joined by commas; caller adds the outer comma/`WITH`.

    exclude_test_business=True 时排除测试营业时段订单 (对齐 ttpos 后台口径)。
    """
    sb_join = ("""LEFT JOIN `{project}`.`{dataset}`.`ttpos_sale_bill` sb
    ON sb.uuid = sp.sale_bill_uuid AND sb.delete_time = 0
  """ if exclude_test_business else "")
    dine_tb = ("\n    " + dine_test_business_clause("sb")) if exclude_test_business else ""
    takeout_tb = ("\n    " + takeout_test_business_clause("t")) if exclude_test_business else ""
    return """price_breakdown_raw AS (
  SELECT
    sp.product_package_uuid AS item_uuid,
    sp.product_sale_price AS price,
    sp.product_num AS qty
  FROM `{project}`.`{dataset}`.`ttpos_statistics_product` sp
  """ + sb_join + """WHERE sp.complete_time >= {start_ts}
    AND sp.complete_time < {end_ts}""" + dine_tb + """
  UNION ALL
  -- 外卖端：排除 state=60 取消订单（让价格档加总能跟营业额对账；取消数另列展示）
  SELECT
    toi.ttpos_product_package_uuid AS item_uuid,
    toi.price AS price,
    toi.quantity AS qty
  FROM `{project}`.`{dataset}`.`ttpos_takeout_order_item` toi
  JOIN `{project}`.`{dataset}`.`ttpos_takeout_order` t
    ON t.uuid = toi.takeout_order_uuid AND t.delete_time = 0
  WHERE toi.delete_time = 0
    AND toi.ttpos_product_package_uuid > 0
    AND t.order_state IN (10, 20, 30, 40)
    AND t.accepted_time > 0
    AND (
      (t.order_state = 40 AND t.completed_time >= {start_ts} AND t.completed_time < {end_ts})
      OR (t.order_state != 40 AND t.accepted_time >= {start_ts} AND t.accepted_time < {end_ts})
    )""" + takeout_tb + """
),
price_breakdown AS (
  SELECT item_uuid, price, SUM(qty) AS qty
  FROM price_breakdown_raw
  GROUP BY item_uuid, price
),
price_ranked AS (
  SELECT
    item_uuid,
    price,
    qty,
    ROW_NUMBER() OVER (PARTITION BY item_uuid ORDER BY qty DESC) AS rn
  FROM price_breakdown
),
price_top3 AS (
  SELECT
    item_uuid,
    MAX(CASE WHEN rn = 1 THEN price END) AS price_1,
    MAX(CASE WHEN rn = 1 THEN qty END) AS qty_1,
    MAX(CASE WHEN rn = 2 THEN price END) AS price_2,
    MAX(CASE WHEN rn = 2 THEN qty END) AS qty_2,
    MAX(CASE WHEN rn = 3 THEN price END) AS price_3,
    MAX(CASE WHEN rn = 3 THEN qty END) AS qty_3,
    SUM(CASE WHEN rn > 3 THEN qty ELSE 0 END) AS other_qty
  FROM price_ranked
  GROUP BY item_uuid
)"""
