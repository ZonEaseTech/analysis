"""Shop (dine-in) sales entity — per-item aggregation from ttpos_statistics_product.

Owns the **ttpos CountProductSale** semantic:
  actual_sale_amount = SUM(IF(free|give, 0, final_price * (num - refund_num)))
sourced from ttpos-server-go/main/app/repository/statistics.go:1980-2046
(ExportProductSales — the export-route algorithm, not the RankProduct top-10).

Returned fields per item_uuid:
  qty                ── 销量（含赠送/退款件，跟 ttpos 后台一致）
  sales_price        ── 营业额（标价 × 销量；不扣折扣/退款；含赠品）
  original_amount    ── 标准金额（商品管理标价 × 销量）
  actual_amount      ── 实收（赠送归零，扣退款，用成交价）
  refund_qty         ── 退款件数
  refund_amount      ── 退款标价金额
  avg_member_discount── 平均会员折扣率
  free_qty, give_qty ── 赠品 / 赠送数量
  free_amount,
  give_amount        ── 赠品 / 赠送行的整单标价金额（金额恒等式用）
  discount_amount    ── 调价折扣：(标价-成交价) × 已售件数（金额恒等式用）
  cancelled_qty,
  cancelled_amount   ── 堂食固定 0（POS 直接成交，无取消单概念）
"""

from semantic.dimensions.test_business import dine_test_business_clause


def shop_sales_cte(exclude_test_business: bool = False) -> str:
    """Returns `shop_sales AS (...)` body with {project}/{dataset}/{start_ts}/{end_ts}
    placeholders intact for downstream `.format()` per shop.

    exclude_test_business=True 时多 JOIN ttpos_sale_bill 并加 NOT EXISTS 排除
    sale_bill.create_time 落在 ttpos_business_status_period 区间内的记录
    (对齐 ttpos 后台 ExcludeTestBusinessByBillSQL 口径)。仅对店启用了测试营业开关
    的 dataset 传 True; 普通店传 False 避免无谓 JOIN。
    """
    sb_join = """LEFT JOIN `{project}`.`{dataset}`.`ttpos_sale_bill` sb
    ON sb.uuid = sp.sale_bill_uuid AND sb.delete_time = 0
  """ if exclude_test_business else ""
    tb_clause = ("\n    " + dine_test_business_clause("sb")) if exclude_test_business else ""
    return """shop_sales AS (
  -- ttpos 源码: ttpos-server-go/main/app/repository/statistics.go:1980-2046 (CountProductSale - ExportProductSales 接口真实算法)
  --   GET /statistics/product_sales/export 路由 → service/business.go:845 ExportProductSales
  -- 注意: 不能用 RankProduct (statistics.go:1245) 的 refund_time=0 过滤 —— 那是 top10 排行，跟导出算法不一样
  --   actual_sale_amount = SUM(IF(free|give, 0, final_price * (num - refund_num)))
  --   时间字段: buildCountOpts 默认走 complete_time
  SELECT
    sp.product_package_uuid AS item_uuid,
    SUM(sp.product_num) AS qty,
    -- 营业额：标价 × 销量（不扣折扣、不扣退款、含赠品）
    SUM(sp.product_sale_price * sp.product_num) AS sales_price,
    -- 毛额 (守恒闭环锚): 堂食无 state, 与 sales_price 同式
    SUM(sp.product_sale_price * sp.product_num) AS gross_amount,
    -- 标准金额：商品管理标价 × 销量
    SUM(IFNULL(pp.price, 0) * sp.product_num) AS original_amount,
    -- 实收金额：ttpos CountProductSale 真实口径 — 赠品/赠送归零，扣退款，用成交价
    SUM(IF(sp.free_num > 0 OR sp.give_num > 0, 0,
           sp.product_final_price * (sp.product_num - sp.refund_num))) AS actual_amount,
    SUM(sp.refund_num) AS refund_qty,
    SUM(sp.product_sale_price * sp.refund_num) AS refund_amount,
    AVG(sp.member_order_discount_rate) AS avg_member_discount,
    SUM(sp.free_num) AS free_qty,
    SUM(sp.give_num) AS give_qty,
    -- 赠品行的整单标价金额（ttpos 把整行算赠送，跟 actual_amount 的 IF 同条件）
    SUM(IF(sp.free_num > 0, sp.product_sale_price * sp.product_num, 0)) AS free_amount,
    SUM(IF(sp.give_num > 0, sp.product_sale_price * sp.product_num, 0)) AS give_amount,
    -- 调价折扣：(标价 - 成交价) × 已售件数，跟 actual_amount 同口径（赠送行排除，退款扣除）
    SUM(IF(sp.free_num > 0 OR sp.give_num > 0, 0,
           (sp.product_sale_price - sp.product_final_price) * (sp.product_num - sp.refund_num))) AS discount_amount,
    -- 堂食没有"取消订单"概念（POS 直接成交），固定 0
    0 AS cancelled_qty,
    0 AS cancelled_amount
  FROM `{project}`.`{dataset}`.`ttpos_statistics_product` sp
  """ + sb_join + """LEFT JOIN `{project}`.`{dataset}`.`ttpos_product_package` pp
    ON pp.uuid = sp.product_package_uuid
  WHERE sp.complete_time >= {start_ts}
    AND sp.complete_time < {end_ts}""" + tb_clause + """
  GROUP BY sp.product_package_uuid
)"""
