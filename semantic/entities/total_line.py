"""Total-line entity — FULL OUTER JOIN of shop_sales + takeout_sales.

Composition primitive: any report wanting the **unified per-item view across
channels** uses this instead of redoing the merge math. Encodes two specific
choices that are surprisingly easy to get wrong:

  1. FULL OUTER JOIN — not LEFT (loses takeout-only items) and not INNER
     (loses store-only items). Both single-channel items must survive.
  2. avg_member_discount is recomputed as a qty-weighted average across the
     two channels, with NULLIF guarding the divide-by-zero edge case.

Depends on (as upstream CTEs in the same WITH clause):
  - shop_sales      (semantic/entities/sale_line.shop_sales_cte)
  - takeout_sales   (semantic/entities/takeout_line.takeout_sales_cte)
"""


def merged_cte() -> str:
    """Returns `merged AS (...)` CTE body — no leading/trailing comma."""
    return """merged AS (
  SELECT
    COALESCE(s.item_uuid, t.item_uuid) AS item_uuid,
    IFNULL(s.qty, 0) + IFNULL(t.qty, 0) AS qty,
    -- 实收金额：shop 端真实收 + takeout 端有效订单
    IFNULL(s.actual_amount, 0) + IFNULL(t.actual_amount, 0) AS revenue,
    -- 营业额：标价×销量
    IFNULL(s.sales_price, 0) + IFNULL(t.sales_price, 0) AS sales_price,
    IFNULL(s.original_amount, 0) + IFNULL(t.original_amount, 0) AS original_amount,
    IFNULL(s.refund_qty, 0) + IFNULL(t.refund_qty, 0) AS refund_qty,
    IFNULL(s.refund_amount, 0) + IFNULL(t.refund_amount, 0) AS refund_amount,
    (IFNULL(s.avg_member_discount * s.qty, 0) + IFNULL(t.avg_member_discount * t.qty, 0)) / NULLIF(IFNULL(s.qty, 0) + IFNULL(t.qty, 0), 0) AS avg_member_discount,
    IFNULL(s.free_qty, 0) + IFNULL(t.free_qty, 0) AS free_qty,
    IFNULL(s.give_qty, 0) + IFNULL(t.give_qty, 0) AS give_qty,
    -- 金额恒等式所需的 3 个分项（外卖端固定 0）
    IFNULL(s.free_amount, 0) + IFNULL(t.free_amount, 0) AS free_amount,
    IFNULL(s.give_amount, 0) + IFNULL(t.give_amount, 0) AS give_amount,
    IFNULL(s.discount_amount, 0) + IFNULL(t.discount_amount, 0) AS discount_amount,
    IFNULL(s.cancelled_qty, 0) + IFNULL(t.cancelled_qty, 0) AS cancelled_qty,
    IFNULL(s.cancelled_amount, 0) + IFNULL(t.cancelled_amount, 0) AS cancelled_amount
  FROM shop_sales s
  FULL OUTER JOIN takeout_sales t USING (item_uuid)
)"""
