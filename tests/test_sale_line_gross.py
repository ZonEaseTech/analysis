"""sale_line / takeout_line / total_line gross_amount 投影 — 渲染契约。

任务: 技术债④前半 — 让 profit_margin_report 的毛额守恒变成真校验 (非定义式补齐).
"""
import unittest

import tests._setup  # noqa: F401

from semantic.entities.sale_line import shop_sales_cte
from semantic.entities.takeout_line import takeout_sales_cte
from semantic.entities.total_line import merged_cte


def render(sql: str) -> str:
    return sql.format(project="p", dataset="d", start_ts=1000, end_ts=2000)


class TestSaleLineGrossAmount(unittest.TestCase):
    """shop_sales_cte (sale_line) 必须投影 gross_amount, 与 sale_event 堂食支同式."""

    def setUp(self):
        self.sql = render(f"WITH {shop_sales_cte()} SELECT * FROM shop_sales")

    def test_gross_amount_projected(self):
        # 与 sale_event 堂食支同式 (萨当整数化): SUM(sp.product_sale_price * sp.product_num)
        self.assertIn(
            "CAST(ROUND(SUM(sp.product_sale_price * sp.product_num) * 100) AS INT64) AS gross_amount",
            self.sql,
        )

    def test_gross_amount_after_sales_price(self):
        # gross_amount 在 sales_price 之后, 方便对照
        sp_pos = self.sql.find("AS sales_price")
        ga_pos = self.sql.find("AS gross_amount")
        self.assertGreater(ga_pos, sp_pos, "gross_amount 应在 sales_price 之后")


class TestTakeoutLineGrossAmount(unittest.TestCase):
    """takeout_sales_cte (takeout_line) 必须投影 gross_amount, state-UNCONDITIONED.

    关键: 不能有 IF(t.order_state ...) 包裹 —— gross 全量才能审计 state 枚举完备性.
    """

    def setUp(self):
        self.sql = render(f"WITH {takeout_sales_cte()} SELECT * FROM takeout_sales")

    def test_gross_amount_projected(self):
        # 不分 state 全量 (萨当整数化): SUM(toi.price * toi.quantity)
        self.assertIn(
            "CAST(ROUND(SUM(toi.price * toi.quantity) * 100) AS INT64) AS gross_amount",
            self.sql,
        )

    def test_gross_amount_not_state_conditioned(self):
        # gross_amount 那行不能被 IF(t.order_state ...) 包裹
        lines = self.sql.split("\n")
        gross_lines = [l for l in lines if "gross_amount" in l]
        for line in gross_lines:
            self.assertNotIn(
                "order_state",
                line,
                f"gross_amount 不应受 order_state 条件约束: {line!r}",
            )

    def test_gross_amount_after_sales_price(self):
        sp_pos = self.sql.find("AS sales_price")
        ga_pos = self.sql.find("AS gross_amount")
        self.assertGreater(ga_pos, sp_pos, "gross_amount 应在 sales_price 之后")


class TestMergedCteGrossAmount(unittest.TestCase):
    """merged_cte (total_line) 必须合并 gross_amount = shop + takeout."""

    def setUp(self):
        self.sql = merged_cte()

    def test_gross_amount_combined(self):
        # IFNULL(s.gross_amount, 0) + IFNULL(t.gross_amount, 0) AS gross_amount
        self.assertIn(
            "IFNULL(s.gross_amount, 0) + IFNULL(t.gross_amount, 0) AS gross_amount",
            self.sql,
        )


if __name__ == "__main__":
    unittest.main()
