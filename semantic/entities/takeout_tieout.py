"""Takeout tieout — 外卖订单粒度互证行 (item 级求和 vs 订单级 platform_total)。

为什么独立于 sale_event:
  - platform_total / merchant_charge_fee / merchant_discount 是订单级字段,
    JOIN 到 item 行再 SUM 会按订单内 item 数重复计数 — 订单级量只能在订单粒度比
  - 这是外卖侧的"第二本账": RankTakeoutProduct (item 级, 我们抄的) vs
    CountTakeoutSale (订单级, ttpos 后台真口径), pitfalls §5.1
  - 华莱士当前 merchant 两字段恒为 0; 业务开启费用时 TAKEOUT_TIEOUT 恒等式
    会 fire, 届时实测符号关系再升级口径

时间条件沿用 pitfalls §1.3 动态规则 (state=40 用 completed_time).

Pre-check 核实 (2026-06-12):
  - item 表: ttpos_takeout_order_item (alias toi) — 与计划假设一致
  - FK: toi.takeout_order_uuid = t.uuid — 与计划假设一致
  - item 列: toi.price * toi.quantity — 与计划假设一致
  - soft delete: toi.delete_time = 0 / t.delete_time = 0 — 与 takeout_line.py 口径一致

金额单位: 萨当 (satang, INT64) — 唯一舍入点在本 CTE 输出层 (spec §6 B).
"""


def takeout_tieout_cte() -> str:
    """Returns `takeout_tieout AS (...)`. 订单粒度, 一行一单。"""
    return """takeout_tieout AS (
  SELECT
    t.uuid AS order_uuid,
    t.order_state AS order_state,
    CAST(ROUND(IFNULL(t.platform_total, 0) * 100) AS INT64) AS platform_total,
    CAST(ROUND(IFNULL(t.merchant_charge_fee, 0) * 100) AS INT64) AS merchant_charge_fee,
    CAST(ROUND(IFNULL(t.merchant_discount, 0) * 100) AS INT64) AS merchant_discount,
    CAST(ROUND(SUM(toi.price * toi.quantity) * 100) AS INT64) AS item_sum
  FROM `{project}`.`{dataset}`.`ttpos_takeout_order` t
  JOIN `{project}`.`{dataset}`.`ttpos_takeout_order_item` toi
    ON toi.takeout_order_uuid = t.uuid AND toi.delete_time = 0
  WHERE t.delete_time = 0
    AND t.accepted_time > 0
    AND (
      (t.order_state = 40 AND t.completed_time >= {start_ts} AND t.completed_time < {end_ts})
      OR
      (t.order_state != 40 AND t.accepted_time >= {start_ts} AND t.accepted_time < {end_ts})
    )
  GROUP BY order_uuid, order_state, platform_total, merchant_charge_fee, merchant_discount
)"""
