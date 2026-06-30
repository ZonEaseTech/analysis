"""Pin down `sale_event` CTE — the finest-grain entity for cross-grain reports.

The headline business clauses are nearly identical to sale_line + takeout_line
but with one extra GROUP BY column (price). These tests anchor the differences
so the two trees can be merged in Phase 2 without surprises.
"""
import unittest

from tests import _setup  # noqa: F401

from semantic.entities import sale_event


def render(sql: str, **kw) -> str:
    defaults = dict(project="p", dataset="d", start_ts=1_700_000_000, end_ts=1_700_864_000)
    defaults.update(kw)
    return sql.format(**defaults)


class SaleEventCteTests(unittest.TestCase):

    def setUp(self):
        # Wrap the CTE body in a minimal WITH .. SELECT so it can be rendered & inspected.
        self.sql = render(f"WITH {sale_event.sale_event_cte()} SELECT * FROM sale_event")

    def test_groups_by_item_and_price_not_just_item(self):
        """Headline contract: finer grain than sale_line. Both halves of UNION must
        group by (item_uuid, price)."""
        # 两次出现：堂食 + 外卖各一次
        self.assertEqual(self.sql.count("GROUP BY item_uuid, price"), 2)

    def test_channel_label(self):
        """Each row must be tagged 'dine' or 'takeout' so consumers can pivot/filter."""
        self.assertIn("'dine' AS channel", self.sql)
        self.assertIn("'takeout' AS channel", self.sql)

    def test_dine_actual_amount_uses_count_product_sale_semantic(self):
        """The 'free|give → 0, deduct refund' rule MUST survive the grain change.
        Otherwise sale_event diverges from sale_line's contract."""
        self.assertIn(
            "CAST(ROUND(SUM(IF(sp.free_num > 0 OR sp.give_num > 0, 0,\n"
            "           sp.product_final_price * (sp.product_num - sp.refund_num))) * 100) AS INT64) AS actual_amount",
            self.sql,
        )

    def test_takeout_excludes_state_60_from_revenue(self):
        self.assertIn(
            "CAST(ROUND(SUM(IF(t.order_state IN (10,20,30,40), toi.price * toi.quantity, 0)) * 100) AS INT64) AS sales_price",
            self.sql,
        )
        self.assertIn(
            "SUM(IF(t.order_state = 60, toi.quantity, 0)) AS cancelled_qty",
            self.sql,
        )

    def test_takeout_dynamic_time_window(self):
        """state=40 → completed_time, others → accepted_time. Same as takeout_line."""
        self.assertIn(
            "(t.order_state = 40 AND t.completed_time >= 1700000000 AND t.completed_time < 1700864000)",
            self.sql,
        )
        self.assertIn(
            "(t.order_state != 40 AND t.accepted_time >= 1700000000 AND t.accepted_time < 1700864000)",
            self.sql,
        )

    def test_takeout_includes_state_60_in_where(self):
        """state=60 (cancelled) must be in the WHERE allow-list so we can split it
        off as cancelled_qty/amount. If filtered out, we lose cancellation visibility."""
        self.assertIn("t.order_state IN (10, 20, 30, 40, 60)", self.sql)

    def test_identity_fields_present_on_dine_side(self):
        """Accounting-identity additions: free_amount / give_amount / discount_amount."""
        self.assertIn(
            "CAST(ROUND(SUM(IF(sp.free_num > 0, sp.product_sale_price * sp.product_num, 0)) * 100) AS INT64) AS free_amount",
            self.sql,
        )
        self.assertIn(
            "CAST(ROUND(SUM(IF(sp.give_num > 0, sp.product_sale_price * sp.product_num, 0)) * 100) AS INT64) AS give_amount",
            self.sql,
        )
        self.assertIn(
            "(sp.product_sale_price - sp.product_final_price) * (sp.product_num - sp.refund_num))) * 100) AS INT64) AS discount_amount",
            self.sql,
        )

    def test_takeout_zeros_identity_fields(self):
        """Takeout has no free/give/discount concept — must zero them so schema
        aligns for UNION ALL (萨当 INT64 对齐)."""
        self.assertIn("CAST(0 AS INT64) AS free_amount", self.sql)
        self.assertIn("CAST(0 AS INT64) AS give_amount", self.sql)
        self.assertIn("CAST(0 AS INT64) AS discount_amount", self.sql)

    def test_gross_amount_dine(self):
        # 堂食: gross == 标价×销量 (无 state 概念, 与 sales_price 同式; 萨当整数化)
        self.assertIn(
            "CAST(ROUND(SUM(sp.product_sale_price * sp.product_num) * 100) AS INT64) AS gross_amount", self.sql)

    def test_gross_amount_takeout_unconditioned(self):
        # 外卖: 不分 state 全量 — 这是守恒闭环的关键, 不许加 IF(order_state ...)
        self.assertIn("CAST(ROUND(SUM(toi.price * toi.quantity) * 100) AS INT64) AS gross_amount", self.sql)


class MetricAndDimensionDeclarationTests(unittest.TestCase):
    """The module-level lists drive aggregate_by_grain; if they drift from the
    SQL the aggregator silently wrong. This test pins them."""

    def test_dimensions_match_select_clauses(self):
        for dim in sale_event.DIMENSION_COLUMNS:
            self.assertIn(f"AS {dim}", sale_event.sale_event_cte(),
                          f"dimension '{dim}' declared but not selected by CTE")

    def test_metric_columns_match_select_clauses(self):
        for metric in sale_event.METRIC_COLUMNS:
            self.assertIn(f"AS {metric}", sale_event.sale_event_cte(),
                          f"metric '{metric}' declared but not selected by CTE")

    def test_no_overlap_between_dim_and_metric(self):
        self.assertEqual(
            set(sale_event.DIMENSION_COLUMNS) & set(sale_event.METRIC_COLUMNS), set(),
            "a column can't be both a dimension and a metric")


if __name__ == "__main__":
    unittest.main()
