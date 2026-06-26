"""订单级折扣分摊 (堂食) — 把 ttpos_sale_order 的 7 项营销减项, 按订单内每条 line
的实收(成交价 × 实付件)比例, 分摊到 item_uuid 粒度。

镜像 scripts/adhoc/merge3_pricelevel_discount.py 已对账的口径
(spec: docs/superpowers/specs/2026-06-13-cost-table-price-level-discount-correction-design.md)。

口径背景:
  sale_event.actual_amount = product_final_price × 实付件 = **应收**(只反映行级成交价,
  没减整单优惠券/会员折扣等 7 项)。真·实收 = 应收 − 本文件分摊出的 order_discount。

折扣 7 项 (全挂 ttpos_sale_order 整单级):
  coupon_amount        优惠券
  member_discount_fee  会员折扣
  custom_discount_fee  自定义折扣
  activity_amount      活动
  gift_amount          赠送
  pay_points_amount    积分抵扣
  zero_checkout_fee    抹零

分摊算法: 订单 O 的总折扣 disc(O) 按订单内每条 line 的实收占比摊下去 ——
  alloc(line) = disc(O) × line_rev / Σ_line_rev(O)
  → 订单内 100% 摊完, 0 丢失; 退货/外卖口径残差诚实保留不分摊。

外卖侧无此 7 项概念 (ttpos_takeout_order 不采), order_discount 仅堂食。
"""

# 7 项营销减项之和 (真源, 改这里就够)
DISCOUNT_EXPR = (
    "coupon_amount + member_discount_fee + custom_discount_fee "
    "+ activity_amount + gift_amount + pay_points_amount + zero_checkout_fee"
)


def order_discount_sql() -> str:
    """Returns 完整 `WITH … SELECT …`。输出列: item_uuid / order_discount。
    占位符 {project}/{dataset}/{start_ts}/{end_ts} 与 sale_event 一致, drop-in engine.query。"""
    return """
WITH line AS (
  SELECT
    sp.sale_order_uuid AS so,
    sp.product_package_uuid AS item_uuid,
    -- 行实收 (赠送归零, 扣退款, 成交价) —— 分摊分母
    IF(sp.free_num > 0 OR sp.give_num > 0, 0,
       sp.product_final_price * (sp.product_num - sp.refund_num)) AS rev
  FROM `{project}`.`{dataset}`.`ttpos_statistics_product` sp
  WHERE sp.complete_time >= {start_ts}
    AND sp.complete_time < {end_ts}
),
od AS (
  SELECT uuid AS so,
    (""" + DISCOUNT_EXPR + """) AS disc
  FROM `{project}`.`{dataset}`.`ttpos_sale_order`
),
ot AS (
  SELECT so, SUM(rev) AS tot FROM line GROUP BY so
)
SELECT
  l.item_uuid AS item_uuid,
  -- 萨当整数化 (与 sale_event 一致, 唯一舍入点在输出层); net_received = actual_amount(萨当) − 本列
  CAST(ROUND(SUM(od.disc * SAFE_DIVIDE(l.rev, NULLIF(ot.tot, 0))) * 100) AS INT64) AS order_discount
FROM line l
JOIN od ON od.so = l.so
JOIN ot ON ot.so = l.so
GROUP BY l.item_uuid
"""
