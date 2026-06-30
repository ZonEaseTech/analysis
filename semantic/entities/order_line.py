"""Order line — 凭证账 (sale_bill → sale_order → sale_order_product)。

跟 sale_event (统计账, ttpos_statistics_product) 的关系:
  两者由 ttpos 后端**不同代码路径**写入, 是天然的两本账. CROSS_LEDGER
  恒等式 (semantic/validators/identities.py) 用本 CTE 对统计账做独立互证 —
  这是销量/金额恒等式从"循环永真"升级为"可证伪"的来源 (spec §3/§5 A1).

口径要点:
  - 只取已完成账单 (sb.status = 1), 时间窗在 sb.finish_time 上.
    与统计账 sp.complete_time 的对齐度由 2026-05 观察跑实测
    (scripts/adhoc/audit_cross_ledger_202605.py), 实测前 CROSS_LEDGER
    不进阻断名单.
  - 三表全部过 delete_time = 0 (ttpos 软删约定).
  - voucher_gross 用 sop.sale_price (折前标价) × num, 对齐统计账
    sales_price = product_sale_price × product_num 的折前口径.
  - sop.status (送厨状态) 不过滤: sb.status=1 已完成账单的商品行定义上已送厨,
    显式说明此处是有意省略, 非遗漏.
  - sop.product_type != 2 排除套餐子行 (2=子行, package_uuid 指向父行 uuid):
    统计账记 SKU 粒度, 凭证账不排子行会按套餐组件翻倍
    (2026-05 基线 31.5% 匹配率的根因, shop005 实测排除后残差 0.00%).
  - 外卖路径 (PR-C): ttpos_takeout_order_item toi, 对齐 sale_event takeout 口径
    (state IN(10,20,30,40)、§1.3 动态时间、package_uuid>0 排未映射商品).
    toi 的 ttpos_product_type 0=单品 1=套餐父均为顶层, toi 无子行无双计数
    (实测含 type=1 时 shop003 96.9% vs 仅 type=0 的 71.7%). 凭证账由此从
    dine-only 扩到全渠道, 跨账本 qty 45.5%→89.5%. 残余 ~10.5% 为结构天花板
    (后端 sp/sop 写入路径不对称等, 见 docs/audit/2026-06-cross-ledger-baseline.md).

金额单位: 萨当 (satang, INT64) — 唯一舍入点在本 CTE 输出层 (spec §6 B).
"""


def order_line_cte() -> str:
    """Returns `order_line AS (...)`. 占位符同 sale_event — drop-in 兼容 engine.query()。

    dine + takeout 两个凭证账分支 UNION ALL 后外层 GROUP BY item_uuid 合并:
    同一商品的堂食与外卖凭证量合成一行, 对齐统计账 sale_event 的 (dine UNION takeout).
    """
    return """order_line AS (
  SELECT
    item_uuid,
    SUM(voucher_qty) AS voucher_qty,
    SUM(voucher_gross) AS voucher_gross,
    SUM(voucher_net) AS voucher_net,
    SUM(voucher_discount) AS voucher_discount
  FROM (
    -- 堂食凭证账: sale_bill → sale_order → sale_order_product (排套餐子行 product_type=2)
    SELECT
      sop.product_package_uuid AS item_uuid,
      SUM(sop.num) AS voucher_qty,
      CAST(ROUND(SUM(sop.sale_price * sop.num) * 100) AS INT64) AS voucher_gross,
      CAST(ROUND(SUM(sop.total_price) * 100) AS INT64) AS voucher_net,
      CAST(ROUND(SUM(sop.discount_fee) * 100) AS INT64) AS voucher_discount
    FROM `{project}`.`{dataset}`.`ttpos_sale_order_product` sop
    JOIN `{project}`.`{dataset}`.`ttpos_sale_order` so
      ON so.uuid = sop.sale_order_uuid AND so.delete_time = 0
    JOIN `{project}`.`{dataset}`.`ttpos_sale_bill` sb
      ON sb.uuid = so.sale_bill_uuid AND sb.delete_time = 0
    WHERE sb.status = 1
      AND sb.finish_time >= {start_ts}
      AND sb.finish_time < {end_ts}
      AND sop.delete_time = 0
      AND sop.product_type != 2
    GROUP BY item_uuid

    UNION ALL

    -- 外卖凭证账: ttpos_takeout_order_item (toi 无子行, type 0/1 均顶层无双计数)
    SELECT
      toi.ttpos_product_package_uuid AS item_uuid,
      SUM(toi.quantity) AS voucher_qty,
      CAST(ROUND(SUM(toi.price * toi.quantity) * 100) AS INT64) AS voucher_gross,
      CAST(ROUND(SUM(toi.price * toi.quantity) * 100) AS INT64) AS voucher_net,
      CAST(0 AS INT64) AS voucher_discount
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
      )
    GROUP BY item_uuid
  )
  GROUP BY item_uuid
)"""
