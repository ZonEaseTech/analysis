"""Payment Bridge Check — ttpos 成本表 vs 汇总表 (turnover vs payment_collected) 对账锚.

从 scripts/adhoc/recon_cost_vs_summary_bridge.py (2026-06-12, 已验证残差抹零级) 提取桥公式和 SQL,
升为正式 reconciliation check。

桥 (完备版, 经 2026-04 回归补全):
  差 = coupon + 其他营销折扣(activity/member/custom/rounding/gift/pay_points)
      − (商品退款 gap: sale_price×refund_num − refund_amount)
      + (外卖口径 gap: subtotal − platform_total)
  残差 抹零级。

口径 (全 BQ 同源, 三表不重叠):
  - 成本表 (turnover) = SP_revenue(final_price×(num−refund), 免单赠送=0) + 外卖 subtotal(price×qty)
    → ttpos CountProductSales + CountTakeoutSale(subtotal)
  - 汇总表 (payment_collected) = SS_received(payment_amount−refund−balance) + 外卖 platform_total
    → ttpos CountSale + CountTakeoutSale(platform_total)

用法:
  check = PaymentBridgeCheck(client, project_id, datasets, start_ts, end_ts)
  result = check.run()
  result.print_summary()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PaymentBridgeTotals:
    """支付桥的六大组件的汇总值 (所有门店 aggregation)。"""
    sp_revenue: float = 0.0       # 堂食营业额 (成本表口径)
    sp_product_refund: float = 0.0  # 堂食商品退款 (标价 × refund_num)
    ss_received: float = 0.0      # 堂食实收 (汇总表口径, payment−refund−balance)
    ss_refund: float = 0.0        # 堂食支付退款 amount
    ss_balance: float = 0.0       # 堂食挂账
    takeout_subtotal: float = 0.0  # 外卖 subtotal (price×qty)
    takeout_platform_total: float = 0.0  # 外卖 platform_total
    coupon: float = 0.0           # 优惠券 (ttpos_sale_order.coupon_amount)
    other_discount: float = 0.0   # 其他营销折扣 (member+custom+activity+gift+pay_points+rounding)

    @property
    def turnover(self) -> float:
        """成本表口径的营业总额."""
        return self.sp_revenue + self.takeout_subtotal

    @property
    def payment_collected(self) -> float:
        """汇总表口径的支付净额."""
        return self.ss_received + self.takeout_platform_total

    @property
    def gap(self) -> float:
        """原始差 (turnover − payment_collected)."""
        return self.turnover - self.payment_collected

    @property
    def refund_gap(self) -> float:
        """退款口径差: 商品退款(标价×数量) − 支付退款金额."""
        return self.sp_product_refund - self.ss_refund

    @property
    def takeout_gap(self) -> float:
        """外卖口径差: subtotal − platform_total."""
        return self.takeout_subtotal - self.takeout_platform_total

    @property
    def predicted_gap(self) -> float:
        """桥预测的差额 = 7项订单折扣 − 退款口径差 + 外卖口径差."""
        return (self.coupon + self.other_discount
                - self.refund_gap + self.takeout_gap)

    @property
    def residue(self) -> float:
        """桥残差 = 实际差 − 预测差 (应 ≈0, 抹零级)."""
        return self.gap - self.predicted_gap


@dataclass
class PaymentBridgeResult:
    """单店对账结果。"""
    store_id: str
    totals: PaymentBridgeTotals = field(default_factory=PaymentBridgeTotals)

    @property
    def residue(self) -> float:
        return self.totals.residue

    def is_drift(self, abs_tol: float = 100.0) -> bool:
        """残差是否超过容忍阈值 (默认 100 THB)."""
        return abs(self.residue) > abs_tol


# ── SQL queries (从 bridge 脚本提取, 移除了硬编码 store UUID 循环) ──

DINE_REVENUE_SQL = """\
SELECT
  IFNULL(SUM(IF(free_num>0 OR give_num>0, 0,
                 product_final_price * (product_num - refund_num))), 0) AS rev,
  IFNULL(SUM(product_sale_price * refund_num), 0) AS prod_refund
FROM `{project}`.`{dataset}`.`ttpos_statistics_product`
WHERE complete_time >= {start_ts} AND complete_time < {end_ts}
"""

DINE_RECEIVED_SQL = """\
SELECT
  IFNULL(SUM(payment_amount - refund_amount - payment_balance), 0) AS recv,
  IFNULL(SUM(refund_amount), 0) AS refund,
  IFNULL(SUM(payment_balance), 0) AS balance
FROM `{project}`.`{dataset}`.`ttpos_statistics_sale`
WHERE complete_time >= {start_ts} AND complete_time < {end_ts}
  AND delete_time = 0
"""

TAKEOUT_SALES_SQL = """\
SELECT
  IFNULL(SUM(IF(order_state IN (10,20,30,40), subtotal, 0)), 0) AS subtotal,
  IFNULL(SUM(IF(order_state IN (10,20,30,40), platform_total, 0)), 0) AS platform_total
FROM `{project}`.`{dataset}`.`ttpos_takeout_order`
WHERE delete_time = 0 AND accepted_time > 0
  AND ((order_state = 40 AND completed_time >= {start_ts} AND completed_time < {end_ts})
       OR (order_state IN (10,20,30) AND accepted_time >= {start_ts} AND accepted_time < {end_ts}))
"""

ORDER_DISCOUNT_SQL = """\
SELECT
  IFNULL(SUM(coupon_amount), 0) AS coupon,
  IFNULL(SUM(member_discount_fee + custom_discount_fee + activity_amount
             + gift_amount + pay_points_amount + zero_checkout_fee), 0) AS other
FROM `{project}`.`{dataset}`.`ttpos_sale_order`
WHERE uuid IN (
  SELECT DISTINCT sale_order_uuid
  FROM `{project}`.`{dataset}`.`ttpos_statistics_product`
  WHERE complete_time >= {start_ts} AND complete_time < {end_ts}
)
"""


class PaymentBridgeCheck:
    """支付桥对账 Check — 验证 turnover 与 payment_collected 的一致性。

    仿 ttpos_anchor.py 的 TtposAnchorCheck 模式：dataclass + run()。
    输出单店 + 全量 aggregation, 残差超过容忍阈值时打印 drift。

    Attributes:
        client: BQ 客户端 (需有 .query(sql).result() 方法).
        project_id: BQ project ID.
        datasets: list of shop datasets (e.g. ["shop1958987436032000", ...]).
        start_ts: 开始时间戳 (unix seconds, int).
        end_ts: 结束时间戳 (unix seconds, int).
    """

    def __init__(self, client, project_id: str, datasets: list[str],
                 start_ts: int, end_ts: int):
        self.client = client
        self.project_id = project_id
        self.datasets = datasets
        self.start_ts = start_ts
        self.end_ts = end_ts

    def _fmt(self, sql: str, dataset: str) -> str:
        return sql.format(project=self.project_id, dataset=dataset,
                          start_ts=self.start_ts, end_ts=self.end_ts)

    def run(self, *, abs_tol: float = 100.0) -> list[PaymentBridgeResult]:
        """跑所有门店的对账, 返回 per-store result 列表。"""
        results: list[PaymentBridgeResult] = []
        for ds in self.datasets:
            res = PaymentBridgeResult(store_id=ds)
            try:
                r1 = list(self.client.query(self._fmt(DINE_REVENUE_SQL, ds)).result())
                if r1:
                    res.totals.sp_revenue = float(r1[0].get("rev") or 0)
                    res.totals.sp_product_refund = float(r1[0].get("prod_refund") or 0)

                r2 = list(self.client.query(self._fmt(DINE_RECEIVED_SQL, ds)).result())
                if r2:
                    res.totals.ss_received = float(r2[0].get("recv") or 0)
                    res.totals.ss_refund = float(r2[0].get("refund") or 0)
                    res.totals.ss_balance = float(r2[0].get("balance") or 0)

                r3 = list(self.client.query(self._fmt(TAKEOUT_SALES_SQL, ds)).result())
                if r3:
                    res.totals.takeout_subtotal = float(r3[0].get("subtotal") or 0)
                    res.totals.takeout_platform_total = float(r3[0].get("platform_total") or 0)

                r4 = list(self.client.query(self._fmt(ORDER_DISCOUNT_SQL, ds)).result())
                if r4:
                    res.totals.coupon = float(r4[0].get("coupon") or 0)
                    res.totals.other_discount = float(r4[0].get("other") or 0)
            except Exception as e:
                print(f"[PaymentBridge] {ds}: query error {e}")

            results.append(res)
        return results

    def aggregate(self, results: list[PaymentBridgeResult]) -> PaymentBridgeTotals:
        """聚合所有门店的 bridge totals。"""
        t = PaymentBridgeTotals()
        for r in results:
            t.sp_revenue += r.totals.sp_revenue
            t.sp_product_refund += r.totals.sp_product_refund
            t.ss_received += r.totals.ss_received
            t.ss_refund += r.totals.ss_refund
            t.ss_balance += r.totals.ss_balance
            t.takeout_subtotal += r.totals.takeout_subtotal
            t.takeout_platform_total += r.totals.takeout_platform_total
            t.coupon += r.totals.coupon
            t.other_discount += r.totals.other_discount
        return t

    def print_summary(self, results: list[PaymentBridgeResult],
                      abs_tol: float = 100.0) -> None:
        """打印对账摘要 (全量 aggregation + per-store residue top-N)."""
        t = self.aggregate(results)
        print(f"Payment Bridge 对账 {len(self.datasets)} 店")
        print(f"  成本表(营业额)  = {t.turnover:,.2f}")
        print(f"  汇总表(支付净额) = {t.payment_collected:,.2f}")
        print(f"  原始差           = {t.gap:,.2f}")
        print(f"  桥: 券 {t.coupon:,.2f} + 其他折扣 {t.other_discount:,.2f}"
              f" − 退货口径差 {t.refund_gap:,.2f} + 外卖口径差 {t.takeout_gap:,.2f}"
              f" = {t.predicted_gap:,.2f}")
        print(f"  残差 = {t.residue:,.2f}")

        drifts = [r for r in results if r.is_drift(abs_tol)]
        if drifts:
            print(f"\n  ⚠️ {len(drifts)} 店残差超过 {abs_tol} THB:")
            for r in sorted(drifts, key=lambda x: abs(x.residue), reverse=True)[:10]:
                print(f"    {r.store_id}: {r.residue:,.2f}")
        else:
            print(f"  全部 {len(results)} 店残差 ≤ {abs_tol} THB 🟢")
