"""TtposAnchorCheck — 对账锚: BQ P&L Net Sales == ttpos 后台 CountSale 数字.

业务价值:
  - 验证 sale_event entity 的 actual_amount 跟 ttpos 后端口径一致
  - 给客户/财务"我们的数字跟 ttpos 后台一致"的硬证明
  - 揭示口径漂移 (e.g. takeout 用 toi.price * qty 而 ttpos 用 platform_total)

ttpos 后端算法 (Go 源码引用):
  - CountSale       statistics.go:141-240
  - CountTakeoutSale statistics_takeout.go:200-286
  - CountTakeoutSale 用 `platform_total` (= subtotal + merchant_charge_fee
    - merchant_discount), 而 sale_event 外卖侧用 `toi.price * toi.quantity`
  - 在华莱士业务下 (merchant_charge_fee = merchant_discount = 0) 两者数值一致

跨进程对账 (调 ttpos HTTP 接口) vs 跨 SQL 对账:
  - 本 check 走"跨 SQL 对账": 直接在 BQ 里复现 ttpos 算法的 SQL, 跑数对比 P&L
  - 优点: 不依赖 ttpos 服务可用 / 凭据 / VPN
  - 缺点: 两侧都是 BQ, 不能验证 ttpos 后端代码本身; 但能验证 sale_event 跟
    ttpos 算法**应该等价**的口径有没有漂移
"""
from __future__ import annotations

from dataclasses import dataclass

from ..base import (
    Discrepancy,
    ReconciliationResult,
    ReconciliationSeverity,
    classify_money_severity,
)


# 跟 ttpos 后端 CountSale + CountTakeoutSale 等价的 SQL.
# 跑出来 = 该店该期间"实收金额"(ttpos 口径).
#
# Shop:    ttpos CountProductSale 算法
#          actual = SUM(IF(free|give, 0, final_price * (num - refund_num)))
#
# Takeout: ttpos CountTakeoutSale 算法
#          actual = SUM(IF(state IN (10,20,30,40), platform_total, 0))
TTPOS_NET_SALES_SQL = """
WITH
shop_sale AS (
  SELECT SUM(
    IF(sp.free_num > 0 OR sp.give_num > 0, 0,
       sp.product_final_price * (sp.product_num - sp.refund_num))
  ) AS amount
  FROM `{project}`.`{dataset}`.`ttpos_statistics_product` sp
  WHERE sp.complete_time >= {start_ts}
    AND sp.complete_time < {end_ts}
),
takeout_sale AS (
  SELECT SUM(
    IF(t.order_state IN (10, 20, 30, 40), t.platform_total, 0)
  ) AS amount
  FROM `{project}`.`{dataset}`.`ttpos_takeout_order` t
  WHERE t.delete_time = 0
    AND t.accepted_time > 0
    AND (
      (t.order_state = 40 AND t.completed_time >= {start_ts} AND t.completed_time < {end_ts})
      OR (t.order_state != 40 AND t.accepted_time >= {start_ts} AND t.accepted_time < {end_ts})
    )
)
SELECT
  IFNULL((SELECT amount FROM shop_sale), 0)
  + IFNULL((SELECT amount FROM takeout_sale), 0) AS ttpos_net_sales
"""


@dataclass
class TtposAnchorCheck:
    """对账 Check: BQ P&L Net Sales 跟 ttpos 后端等价 SQL 数字对比.

    用法:
        # main 流程里跑独立 SQL 拿 ttpos 数字
        ttpos_amount = _fetch_ttpos_net_sales(engine, merchants, start, end)
        check = TtposAnchorCheck(
            name="TTPOS Anchor 2026-04",
            bq_net_sales=artifact["pnl"].by_code("net_sales").amount,
            ttpos_net_sales=ttpos_amount,
        )
        result = check.run()

    Severity 阈值 (对账锚极严):
      < 1 元              → NEGLIGIBLE
      < 100 元 / 0.1%     → NEEDS_REVIEW
      > 0.1%              → MUST_FIX
    """

    name: str
    bq_net_sales: float
    ttpos_net_sales: float

    def run(self) -> ReconciliationResult:
        delta = self.bq_net_sales - self.ttpos_net_sales
        severity = classify_money_severity(
            abs_delta=delta,
            base=self.ttpos_net_sales,
            negligible_abs=1.0,
            negligible_rel=0.00001,   # 0.001% 内忽略 (浮点累积)
            review_abs=100.0,
            review_rel=0.0001,        # 0.01% 复核
            fatal_rel=0.001,          # 0.1% 必查
        )

        discrepancies = []
        if severity != ReconciliationSeverity.NEGLIGIBLE:
            rel = abs(delta / self.ttpos_net_sales) if self.ttpos_net_sales else 0
            discrepancies.append(Discrepancy(
                entity_id="Net Sales (全集团)",
                bq_value=self.bq_net_sales,
                external_value=self.ttpos_net_sales,
                note=(
                    f"BQ P&L (sale_event.actual_amount) vs "
                    f"ttpos 后端等价 SQL (CountSale + CountTakeoutSale)\n"
                    f"  delta = {delta:+,.2f} ({rel*100:.4f}%)\n"
                    f"  常见原因: takeout 口径漂移 (toi.price vs platform_total) / "
                    f"店启用了商家服务费"
                ),
            ))

        return ReconciliationResult(
            check_name=self.name,
            total_compared=1,
            discrepancies=discrepancies,
            severity=severity,
            summary=(
                f"BQ {self.bq_net_sales:,.2f}  vs  "
                f"ttpos {self.ttpos_net_sales:,.2f}  "
                f"delta {delta:+,.2f} "
                f"({abs(delta/self.ttpos_net_sales)*100 if self.ttpos_net_sales else 0:.4f}%)"
            ),
        )
