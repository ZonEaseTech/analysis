"""`aggregate_with_bom` — pure function, hand-crafted inputs.

Pins down:
  - combo mode walks combo_structure to roll up child BOMs
  - single mode uses BOM at item_uuid directly
  - same-material across children/duplicates is summed (qty) not multiplied (price)
  - weighted average member_discount across multiple rows
  - cancelled_qty / cancelled_amount carried through
  - resulting bom tuple shape: (code, name, bom_num, unit_price, uom)
"""
import unittest

from tests._setup import order_row  # noqa: F401 — also wires sys.path

from bq_reports.profit_margin_report import aggregate_with_bom


class ComboModeTests(unittest.TestCase):
    """One combo item rolled up from two children sharing a material."""

    def setUp(self):
        self.rows = [
            order_row(
                store_num="001", store_name="店A",
                item_uuid="COMBO1", item_name="套餐A",
                qty=10, revenue=500, sales_price=600, original_amount=580,
                avg_member_discount=0.9, free_qty=1, give_qty=1,
                refund_qty=2, refund_amount=20,
                cancelled_qty=3, cancelled_amount=30,
                list_price=60,
            ),
        ]
        self.bom_data = {
            "001": {
                "CHILD_A": [("M1", "肉", 100.0, "g", 1.0, 0.0)],
                # Shared material across CHILD_A and CHILD_B: qty must SUM, price stays.
                "CHILD_B": [
                    ("M1", "肉",  50.0, "g", 1.0, 0.0),
                    ("M2", "面粉", 30.0, "g", 1.0, 0.0),
                ],
            }
        }
        self.combo_structure = {"001": {"COMBO1": ["CHILD_A", "CHILD_B"]}}
        # Prices supplied via uploaded sheet (highest priority) so we're not
        # implicitly testing the fallback chain here.
        self.uploaded = {"M1": 0.05, "M2": 0.10}

    def test_basic_combo_rollup(self):
        agg = aggregate_with_bom(
            self.rows, self.bom_data, self.combo_structure,
            uploaded_prices=self.uploaded, erp_prices=None, mode="combo",
        )
        key = ("001", "店A", "COMBO1", "套餐A")
        self.assertIn(key, agg)
        v = agg[key]
        # Scalar fields passthrough.
        self.assertEqual(v["qty"], 10)
        self.assertEqual(v["revenue"], 500)
        self.assertEqual(v["sales_price"], 600)
        self.assertEqual(v["cancelled_qty"], 3)
        self.assertEqual(v["cancelled_amount"], 30)
        # BOM shape: list of (code, name, bom_num, unit_price, uom)
        boms = {b[0]: b for b in v["bom"]}
        self.assertEqual(set(boms), {"M1", "M2"})
        # M1: child A 100 + child B 50 = 150, price unchanged at 0.05
        _, name, bom_num, unit_price, uom = boms["M1"]
        self.assertEqual(name, "肉")
        self.assertAlmostEqual(bom_num, 150.0)
        self.assertAlmostEqual(unit_price, 0.05)
        self.assertEqual(uom, "g")
        # M2: only in child B
        self.assertAlmostEqual(boms["M2"][2], 30.0)

    def test_combo_with_no_structure_yields_no_bom(self):
        agg = aggregate_with_bom(
            self.rows, self.bom_data, {"001": {}},
            uploaded_prices=self.uploaded, mode="combo",
        )
        key = ("001", "店A", "COMBO1", "套餐A")
        self.assertEqual(agg[key]["bom"], [])

    def test_weighted_average_discount_across_rows(self):
        rows = [
            order_row(item_uuid="X", qty=10, avg_member_discount=1.0),
            order_row(item_uuid="X", qty=30, avg_member_discount=0.5),
        ]
        agg = aggregate_with_bom(rows, {}, {}, mode="single")
        v = list(agg.values())[0]
        # (10*1.0 + 30*0.5) / 40 = 0.625
        self.assertAlmostEqual(v["avg_member_discount"], 0.625)
        self.assertEqual(v["qty"], 40)


class SingleModeTests(unittest.TestCase):
    def test_single_bom_dedup(self):
        """Same material listed twice (e.g. via multi-flavour bom_card) must dedup."""
        rows = [order_row(item_uuid="ITEM_X", qty=5, revenue=100, list_price=20)]
        bom_data = {
            "001": {
                "ITEM_X": [
                    ("M9", "盐", 1.0, "g", 1.0, 0.0),
                    ("M9", "盐", 1.0, "g", 1.0, 0.0),   # duplicate row
                ]
            }
        }
        agg = aggregate_with_bom(
            rows, bom_data, combo_structure={},
            uploaded_prices={"M9": 0.2}, mode="single",
        )
        v = list(agg.values())[0]
        self.assertEqual(len(v["bom"]), 1, "Duplicate material rows must dedup")
        self.assertEqual(v["bom"][0][2], 1.0, "qty must not double")

    def test_single_uses_item_uuid_not_combo_structure(self):
        rows = [order_row(item_uuid="ITEM_X", qty=1)]
        bom_data = {"001": {"ITEM_X": [("M1", "x", 2.0, "g", 1.0, 0.0)]}}
        combo_structure = {"001": {"ITEM_X": ["WRONG_CHILD"]}}  # must be ignored in single mode
        agg = aggregate_with_bom(rows, bom_data, combo_structure,
                                 uploaded_prices={"M1": 1.0}, mode="single")
        v = list(agg.values())[0]
        self.assertEqual(len(v["bom"]), 1)
        self.assertEqual(v["bom"][0][0], "M1")

    def test_missing_material_code_skipped(self):
        rows = [order_row(item_uuid="ITEM_Y", qty=1)]
        bom_data = {"001": {"ITEM_Y": [("", "blank", 1.0, "g", 1.0, 0.0),
                                        ("M2", "ok", 1.0, "g", 1.0, 0.0)]}}
        agg = aggregate_with_bom(rows, bom_data, {}, uploaded_prices={"M2": 2.0}, mode="single")
        v = list(agg.values())[0]
        codes = [b[0] for b in v["bom"]]
        self.assertEqual(codes, ["M2"])


class PriceResolutionFromAggregate(unittest.TestCase):
    """unit_price in the output = base_price × conv_rate (BQ-side conversion preserved)."""

    def test_conversion_rate_multiplies_base_price(self):
        rows = [order_row(item_uuid="P", qty=1)]
        # bq_price = 100, conversion_rate = 0.001 (e.g. inventory in kg → recipe in g)
        bom_data = {"001": {"P": [("MK00001", "x", 5, "g", 0.001, 100)]}}
        agg = aggregate_with_bom(rows, bom_data, {}, mode="single")
        v = list(agg.values())[0]
        _, _, _, unit_price, _ = v["bom"][0]
        # No uploaded/ERP price → falls back to bq_price 100 × 0.001 = 0.1
        self.assertAlmostEqual(unit_price, 0.1)


if __name__ == "__main__":
    unittest.main()
