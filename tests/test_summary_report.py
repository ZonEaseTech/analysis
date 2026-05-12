"""Pin down _build_summary_rows + net_qty derivation + new SQL fields.

Three layers locked here:
  1. SQL clauses for free_amount / give_amount / discount_amount on shop side
  2. aggregate_with_bom carries them through AND derives net_qty
  3. _build_summary_rows flattens to one row per SKU with pre-computed cost/profit/margin
"""
import unittest

from tests._setup import order_row  # noqa: F401 — sys.path side-effect

from bq_reports.profit_margin_report import (
    COMBO_ORDERS_SQL,
    _build_summary_rows,
    aggregate_with_bom,
)


class NewSqlFieldsTests(unittest.TestCase):
    """The three new identity-supporting fields must be in the rendered SQL."""

    def setUp(self):
        self.sql = COMBO_ORDERS_SQL.format(
            project="p", dataset="d", start_ts=1_700_000_000, end_ts=1_700_864_000)

    def test_free_amount_clause(self):
        self.assertIn(
            "SUM(IF(sp.free_num > 0, sp.product_sale_price * sp.product_num, 0)) AS free_amount",
            self.sql,
        )

    def test_give_amount_clause(self):
        self.assertIn(
            "SUM(IF(sp.give_num > 0, sp.product_sale_price * sp.product_num, 0)) AS give_amount",
            self.sql,
        )

    def test_discount_amount_clause_uses_same_if_as_actual(self):
        """discount must use the same exclusion (free|give → 0, deduct refund)
        as actual_amount, otherwise the金额恒等式 won't balance."""
        self.assertIn(
            "SUM(IF(sp.free_num > 0 OR sp.give_num > 0, 0,\n"
            "           (sp.product_sale_price - sp.product_final_price) * (sp.product_num - sp.refund_num))) AS discount_amount",
            self.sql,
        )

    def test_takeout_side_zeros_new_fields(self):
        """Takeout has no free/give/discount — must zero them to align schema."""
        self.assertIn("0 AS free_amount", self.sql)
        self.assertIn("0 AS give_amount", self.sql)
        self.assertIn("0 AS discount_amount", self.sql)

    def test_merged_cte_aggregates_new_fields(self):
        self.assertIn(
            "IFNULL(s.free_amount, 0) + IFNULL(t.free_amount, 0) AS free_amount",
            self.sql,
        )
        self.assertIn(
            "IFNULL(s.discount_amount, 0) + IFNULL(t.discount_amount, 0) AS discount_amount",
            self.sql,
        )


class NetQtyDerivationTests(unittest.TestCase):
    """net_qty = qty - free - give - refund - cancel (derived in aggregate)."""

    def test_derived_net_qty(self):
        rows = [order_row(qty=20, free_qty=2, give_qty=1,
                          refund_qty=3, cancelled_qty=4)]
        agg = aggregate_with_bom(rows, {}, {}, mode="single")
        v = list(agg.values())[0]
        # 20 - 2 - 1 - 3 - 4 = 10
        self.assertEqual(v["net_qty"], 10)

    def test_net_qty_when_no_loss(self):
        rows = [order_row(qty=5)]
        agg = aggregate_with_bom(rows, {}, {}, mode="single")
        self.assertEqual(list(agg.values())[0]["net_qty"], 5)

    def test_net_qty_can_be_zero(self):
        rows = [order_row(qty=4, refund_qty=4)]
        agg = aggregate_with_bom(rows, {}, {}, mode="single")
        self.assertEqual(list(agg.values())[0]["net_qty"], 0)


class AggregateCarriesIdentityFieldsTests(unittest.TestCase):
    def test_three_new_amounts_summed_per_sku(self):
        rows = [
            order_row(qty=5, free_amount=10, give_amount=20, discount_amount=5),
            order_row(qty=3, free_amount=4,  give_amount=6,  discount_amount=2),
        ]
        agg = aggregate_with_bom(rows, {}, {}, mode="single")
        v = list(agg.values())[0]
        self.assertEqual(v["free_amount"], 14)
        self.assertEqual(v["give_amount"], 26)
        self.assertEqual(v["discount_amount"], 7)


class BuildSummaryRowsTests(unittest.TestCase):
    """Flatten produces one row per (store, SKU) with pre-computed numbers."""

    def _agg(self, bom=None, **fields):
        defaults = dict(
            qty=10.0, net_qty=8.0, revenue=300.0, sales_price=500.0,
            original_amount=480.0, refund_qty=0, refund_amount=0,
            cancelled_qty=0, cancelled_amount=0,
            free_amount=0, give_amount=0, discount_amount=0,
            free_qty=1.0, give_qty=1.0, avg_member_discount=1.0,
            list_price=60.0,
            price_1=None, qty_1=None, price_2=None, qty_2=None,
            price_3=None, qty_3=None, other_price_qty=None,
            bom=bom or [],
        )
        defaults.update(fields)
        return {("001", "店A", "ITEM_X", "商品A"): defaults}

    def test_one_row_per_sku(self):
        rows = _build_summary_rows(self._agg(bom=[
            ("M1", "盐", 1.0, 0.5, "g"),
            ("M2", "糖", 2.0, 0.7, "g"),
        ]), mode="single")
        self.assertEqual(len(rows), 1, "BOM list of 2 must still collapse to 1 row")

    def test_row_shape_12_columns(self):
        rows = _build_summary_rows(self._agg(bom=[("M1", "x", 1.0, 1.0, "g")]),
                                    mode="single")
        self.assertEqual(len(rows[0]), 12)

    def test_per_unit_cost_is_sumproduct(self):
        rows = _build_summary_rows(self._agg(bom=[
            ("M1", "盐", 1.0, 0.5, "g"),     # 0.5
            ("M2", "糖", 2.0, 0.7, "g"),     # 1.4
        ]), mode="single")
        # cost per unit = 0.5 + 1.4 = 1.9
        self.assertAlmostEqual(rows[0][7], 1.9)

    def test_total_cost_uses_gross_qty(self):
        """Total cost should multiply by gross qty (含赠/退/取), because BOM
        physical consumption actually happened for those items too."""
        rows = _build_summary_rows(self._agg(qty=10, net_qty=5,
                                              bom=[("M1", "x", 1.0, 2.0, "g")]),
                                    mode="single")
        # per_unit_cost = 2; total = 2 * 10 = 20
        self.assertEqual(rows[0][8], 20)

    def test_profit_and_margin(self):
        rows = _build_summary_rows(self._agg(qty=10, revenue=300.0,
                                              bom=[("M1", "x", 1.0, 2.0, "g")]),
                                    mode="single")
        # cost=20, profit=300-20=280, margin=280/300
        self.assertEqual(rows[0][9], 280)
        self.assertAlmostEqual(rows[0][10], 280 / 300, places=4)

    def test_zero_revenue_margin_zero_not_div_error(self):
        rows = _build_summary_rows(self._agg(revenue=0,
                                              bom=[("M1", "x", 1, 1, "g")]),
                                    mode="single")
        self.assertEqual(rows[0][10], 0)

    def test_no_bom_zero_cost(self):
        rows = _build_summary_rows(self._agg(bom=[]), mode="single")
        self.assertEqual(rows[0][7], 0)    # per-unit cost
        self.assertEqual(rows[0][8], 0)    # total cost

    def test_item_uuid_in_last_col(self):
        rows = _build_summary_rows(self._agg(bom=[]), mode="single")
        self.assertEqual(rows[0][11], "ITEM_X")


if __name__ == "__main__":
    unittest.main()
