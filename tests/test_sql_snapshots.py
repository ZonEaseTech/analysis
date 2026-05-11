"""SQL snapshot tests — pin _PROFIT_SALES_TPL / BOM_SQL / COMBO_STRUCTURE_SQL.

We don't want to brittle-snapshot the whole SQL string (that'd churn on whitespace).
Instead, we assert on **business-meaningful clauses** that the refactor must preserve.

If you intentionally change a clause, update the matching assertion explicitly so
the diff in PR shows what business rule moved.
"""
import re
import unittest

from tests import _setup  # noqa: F401 — sys.path side-effect

from bq_reports.profit_margin_report import (
    BOM_SQL,
    COMBO_ORDERS_SQL,
    COMBO_STRUCTURE_SQL,
    SINGLE_ORDERS_SQL,
    _PROFIT_SALES_TPL,
)


def render(sql: str, **kw) -> str:
    defaults = dict(project="p", dataset="d", start_ts=1700000000, end_ts=1700864000)
    defaults.update(kw)
    return sql.format(**defaults)


class ProfitSalesTemplateTests(unittest.TestCase):
    """Anchor: 'what does shop_sales / takeout_sales / merged actually compute?'"""

    def test_combo_and_single_share_template(self):
        # The only difference between the two should be product_type substitution.
        self.assertIn("pp.product_type = 1", COMBO_ORDERS_SQL)
        self.assertIn("pp.product_type = 0", SINGLE_ORDERS_SQL)
        # Replacing back yields the original template (modulo the placeholder).
        self.assertEqual(
            COMBO_ORDERS_SQL.replace("product_type = 1", "product_type = {product_type}"),
            _PROFIT_SALES_TPL,
        )

    def test_shop_sales_uses_complete_time_window(self):
        sql = render(COMBO_ORDERS_SQL, product_type=1)
        # shop_sales filters by complete_time; this anchors the "ttpos CountProductSale"
        # contract documented in the SQL comments.
        m = re.search(r"shop_sales AS.*?GROUP BY sp\.product_package_uuid", sql, re.S)
        self.assertIsNotNone(m, "shop_sales CTE missing or mis-named")
        block = m.group(0)
        self.assertIn("sp.complete_time >= 1700000000", block)
        self.assertIn("sp.complete_time < 1700864000", block)

    def test_shop_sales_actual_amount_zeros_free_and_give(self):
        """实收金额：赠品/赠送归零，扣退款，用成交价 — locked semantic."""
        sql = render(COMBO_ORDERS_SQL, product_type=1)
        # Exact substring of the IF condition; if anyone tries to change to OR/AND
        # of different fields this test breaks.
        self.assertIn(
            "SUM(IF(sp.free_num > 0 OR sp.give_num > 0, 0,\n"
            "           sp.product_final_price * (sp.product_num - sp.refund_num))) AS actual_amount",
            sql,
        )

    def test_takeout_sales_dynamic_time_window(self):
        """state=40 uses completed_time, others use accepted_time — ttpos RankTakeoutProduct semantic."""
        sql = render(COMBO_ORDERS_SQL, product_type=1)
        m = re.search(r"takeout_sales AS.*?GROUP BY item_uuid", sql, re.S)
        self.assertIsNotNone(m)
        block = m.group(0)
        # Both branches of the time filter must be present.
        self.assertIn("t.order_state = 40 AND t.completed_time >= 1700000000", block)
        self.assertIn("t.order_state != 40 AND t.accepted_time >= 1700000000", block)
        # state=60 (cancelled) must be in the WHERE allow-list (we want them
        # counted in cancelled_qty / cancelled_amount).
        self.assertIn("t.order_state IN (10, 20, 30, 40, 60)", block)

    def test_takeout_revenue_excludes_state_60(self):
        """state=60 cancellations contribute 0 to sales_price/actual_amount."""
        sql = render(COMBO_ORDERS_SQL, product_type=1)
        self.assertIn(
            "SUM(IF(t.order_state IN (10,20,30,40), toi.price * toi.quantity, 0)) AS sales_price",
            sql,
        )
        self.assertIn(
            "SUM(IF(t.order_state = 60, toi.quantity, 0)) AS cancelled_qty",
            sql,
        )

    def test_price_breakdown_top3_with_other(self):
        sql = render(COMBO_ORDERS_SQL, product_type=1)
        # Rank by qty DESC and bucket >3 into 'other_qty'.
        self.assertIn(
            "ROW_NUMBER() OVER (PARTITION BY item_uuid ORDER BY qty DESC) AS rn", sql
        )
        self.assertIn("SUM(CASE WHEN rn > 3 THEN qty ELSE 0 END) AS other_qty", sql)

    def test_price_breakdown_includes_dine_and_takeout(self):
        """If a refactor splits the union, this regression test fires."""
        sql = render(COMBO_ORDERS_SQL, product_type=1)
        m = re.search(r"price_breakdown_raw AS \((.*?)\),\s*price_breakdown AS", sql, re.S)
        self.assertIsNotNone(m, "price_breakdown_raw CTE structure changed")
        block = m.group(1)
        self.assertIn("ttpos_statistics_product", block)
        self.assertIn("ttpos_takeout_order_item", block)
        self.assertIn("UNION ALL", block)
        # Takeout side must exclude state=60 for price breakdown reconciliation.
        self.assertIn("t.order_state IN (10, 20, 30, 40)", block)

    def test_merged_full_outer_join(self):
        """shop and takeout must be FULL OUTER JOIN so single-channel items still appear."""
        sql = render(COMBO_ORDERS_SQL, product_type=1)
        self.assertRegex(sql, r"FROM shop_sales s\s*\n\s*FULL OUTER JOIN takeout_sales t USING \(item_uuid\)")

    def test_final_select_strips_invisible_whitespace(self):
        sql = render(COMBO_ORDERS_SQL, product_type=1)
        # The trim regex is what dedups names like "X\r" vs "X" — anchor it.
        self.assertIn(r"REGEXP_REPLACE(COALESCE(", sql)
        # Backslash-escaped \\s in the template ({…} format-safe form)
        self.assertIn(r"r'^\s+|\s+$', '')", sql)

    def test_only_keeps_items_with_qty(self):
        sql = render(COMBO_ORDERS_SQL, product_type=1)
        self.assertIn("AND m.qty > 0", sql)


class BomSqlTests(unittest.TestCase):
    def test_keeps_deleted_items_when_no_active_sibling(self):
        """Soft-deleted product_bom rows are kept if the product has no active row."""
        sql = render(BOM_SQL)
        self.assertIn(
            "SUM(CASE WHEN pb.delete_time = 0 THEN 1 ELSE 0 END)\n"
            "      OVER (PARTITION BY pb.product_package_uuid) AS active_count",
            sql,
        )
        self.assertIn("WHERE (pb.delete_time = 0 OR pb.active_count = 0)", sql)

    def test_related_material_or_join(self):
        """JOIN handles both bom-card-driven and direct related rows."""
        sql = render(BOM_SQL)
        self.assertIn("pb.product_bom_card_uuid > 0 AND rm.related_uuid = pb.product_bom_card_uuid", sql)
        self.assertIn("pb.product_bom_card_uuid = 0 AND rm.related_uuid = pb.uuid", sql)


class ComboStructureSqlTests(unittest.TestCase):
    def test_parent_child_join_via_package_uuid(self):
        sql = render(COMBO_STRUCTURE_SQL)
        # parent.product_type = 1 (combo); child links via child.package_uuid = parent.uuid
        self.assertIn("parent_sop.product_type = 1", sql)
        self.assertIn("ON child_sop.package_uuid = parent_sop.uuid", sql)
        # Time bounds use sale_bill.finish_time.
        self.assertIn("sb.finish_time >= 1700000000", sql)
        self.assertIn("sb.finish_time < 1700864000", sql)
        # Must only count finished sales (sb.status = 1).
        self.assertIn("sb.status = 1", sql)


if __name__ == "__main__":
    unittest.main()
