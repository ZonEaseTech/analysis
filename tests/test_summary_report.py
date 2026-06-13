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
            "CAST(ROUND(SUM(IF(sp.free_num > 0, sp.product_sale_price * sp.product_num, 0)) * 100) AS INT64) AS free_amount",
            self.sql,
        )

    def test_give_amount_clause(self):
        self.assertIn(
            "CAST(ROUND(SUM(IF(sp.give_num > 0, sp.product_sale_price * sp.product_num, 0)) * 100) AS INT64) AS give_amount",
            self.sql,
        )

    def test_discount_amount_clause_uses_same_if_as_actual(self):
        """discount must use the same exclusion (free|give → 0, deduct refund)
        as actual_amount, otherwise the金额恒等式 won't balance (萨当整数化)."""
        self.assertIn(
            "CAST(ROUND(SUM(IF(sp.free_num > 0 OR sp.give_num > 0, 0,\n"
            "           (sp.product_sale_price - sp.product_final_price) * (sp.product_num - sp.refund_num))) * 100) AS INT64) AS discount_amount",
            self.sql,
        )

    def test_takeout_side_zeros_new_fields(self):
        """Takeout has no free/give/discount — must zero them to align schema (萨当 INT64)."""
        self.assertIn("CAST(0 AS INT64) AS free_amount", self.sql)
        self.assertIn("CAST(0 AS INT64) AS give_amount", self.sql)
        self.assertIn("CAST(0 AS INT64) AS discount_amount", self.sql)

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

    def test_row_shape_14_columns(self):
        # 12 visible + 2 audit (12=BOM 来源, 13=价来源)
        rows = _build_summary_rows(self._agg(bom=[("M1", "x", 1.0, 1.0, "g")]),
                                    mode="single")
        self.assertEqual(len(rows[0]), 14)
        self.assertEqual(rows[0][12], "bq_native")  # 无 bom_layers, BQ 命中

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


class StrictBomTests(unittest.TestCase):
    """strict_bom — 默认严格: BOM 只走 bom_sources, 缺失 → 来源='无', 不走 BQ 原生.

    commit e01cb4e / 831285d 引入. 跟 strict_price 对称.
    三处实现 (_build_rows / _build_summary_rows / profit_by_price._bom_for_item)
    口径对称, 这里测 _build_summary_rows 主路径锁定行为.
    """

    def _agg(self, bom):
        # 一个 (店, SKU), 带 BQ 原生 bom
        defaults = dict(
            qty=10.0, net_qty=8.0, revenue=300.0, sales_price=500.0,
            original_amount=480.0, refund_qty=0, refund_amount=0,
            cancelled_qty=0, cancelled_amount=0,
            free_amount=0, give_amount=0, discount_amount=0,
            free_qty=0, give_qty=0, avg_member_discount=1.0, list_price=60.0,
            price_1=None, qty_1=None, price_2=None, qty_2=None,
            price_3=None, qty_3=None, other_price_qty=None,
            bom=bom,
        )
        return {("001", "店A", "ITEM_X", "商品A"): defaults}

    BQ_BOM = [("M1", "x", 1.0, 2.0, "g")]   # BQ 原生 BOM, per-unit cost=2

    def test_non_strict_keeps_bq_native(self):
        # 默认 strict_bom=False → 用 BQ 原生, bom_source=bq_native, 有成本
        rows = _build_summary_rows(self._agg(bom=self.BQ_BOM), mode="single")
        self.assertEqual(rows[0][12], "bq_native")
        self.assertEqual(rows[0][8], 20)          # total cost = 2 × qty 10

    def test_strict_bom_discards_bq_native(self):
        # strict_bom=True + 有 BQ 原生 + 无 bom_layers 命中 → 抛弃 BQ 原生
        rows = _build_summary_rows(self._agg(bom=self.BQ_BOM), mode="single",
                                    strict_bom=True)
        self.assertEqual(rows[0][12], "无")        # 不再标 bq_native
        self.assertEqual(rows[0][8], 0)            # 成本归 0 (BQ 原生被抛弃)

    def test_strict_bom_layer_still_hits(self):
        # strict_bom=True 但 bom_layers 命中 → 用 layer, strict 只挡 BQ 原生
        layers = [("补充BOM", 200, {"商品A": [("M9", "盐", 3.0, "g")]}, "exact")]
        rows = _build_summary_rows(self._agg(bom=self.BQ_BOM), mode="single",
                                    bom_layers=layers, strict_bom=True,
                                    strict_price=False)
        self.assertEqual(rows[0][12], "补充BOM")    # 命中外挂层, 不受 strict 影响


if __name__ == "__main__":
    unittest.main()
