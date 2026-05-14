"""`_build_rows` flattens agg_data → list[list].

The column positions here are a **contract** with both `profit_margin.yaml`
(field_index references) and any future Excel column. The refactor must not shift
any field_index without also moving the yaml.

Tail layout (extension slots, indices 26-33):
   26-29 reserved for engine-driven formula columns (单份总成本/单品毛利/总毛利/毛利率)
   30-31 reserved for 标价应收 / 异常损失 formula columns
   32-33 carry cancelled_qty / cancelled_amount values
"""
import unittest

from tests._setup import order_row  # noqa: F401

from bq_reports.profit_margin_report import _build_rows


def make_agg(bom=None, **fields):
    """Minimal agg_data dict matching what aggregate_with_bom returns."""
    defaults = dict(
        qty=10.0, revenue=300.0,
        sales_price=500.0, original_amount=480.0,
        refund_qty=2.0, refund_amount=20.0,
        cancelled_qty=3.0, cancelled_amount=30.0,
        avg_member_discount=0.95,
        free_qty=1.0, give_qty=2.0,
        list_price=60.0,
        price_1=60.0, qty_1=5.0,
        price_2=55.0, qty_2=3.0,
        price_3=50.0, qty_3=2.0,
        other_price_qty=0,
        bom=bom if bom is not None else [],
    )
    defaults.update(fields)
    return {("001", "店A", "ITEM_X", "商品A"): defaults}


class ColumnContractTests(unittest.TestCase):
    """Position-by-position assertions — these mirror the YAML field_index map."""

    EXPECTED_LEN = 26 + 10  # base layout + 10-slot tail (含 34=bom_source, 35=price_source)

    def test_row_length_with_bom(self):
        agg = make_agg(bom=[("M1", "盐", 1.0, 0.5, "g")])
        rows = _build_rows(agg, mode="single")
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), self.EXPECTED_LEN)

    def test_row_length_without_bom_still_emits_one_row(self):
        rows = _build_rows(make_agg(bom=[]), mode="single")
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), self.EXPECTED_LEN)

    def test_field_positions_match_yaml_contract(self):
        agg = make_agg(bom=[("M1", "盐", 1.234, 0.5, "g")])
        row = _build_rows(agg, mode="single")[0]
        # 0-2 identity
        self.assertEqual(row[0], "001")
        self.assertEqual(row[1], "店A")
        self.assertEqual(row[2], "商品A")
        # 3 当前标价, 4 销量
        self.assertEqual(row[3], 60.0)
        self.assertEqual(row[4], 10.0)
        # 5 营业额, 6 标准金额, 7 实收, 8 折扣率, 9/10 free/give, 11/12 refund qty/amount
        self.assertEqual(row[5], 500.0)
        self.assertEqual(row[6], 480.0)
        self.assertEqual(row[7], 300.0)
        self.assertEqual(row[8], 0.95)
        self.assertEqual(row[9], 1.0)
        self.assertEqual(row[10], 2.0)
        self.assertEqual(row[11], 2.0)
        self.assertEqual(row[12], 20.0)
        # 13-19 price breakdown
        self.assertEqual(row[13], 60.0)
        self.assertEqual(row[14], 5.0)
        self.assertEqual(row[15], 55.0)
        self.assertEqual(row[16], 3.0)
        self.assertEqual(row[17], 50.0)
        self.assertEqual(row[18], 2.0)
        self.assertEqual(row[19], 0)
        # 20-24 BOM (name, code, qty, unit_price, uom)
        self.assertEqual(row[20], "盐")
        self.assertEqual(row[21], "M1")
        self.assertEqual(row[22], 1.234)
        self.assertEqual(row[23], 0.5)
        self.assertEqual(row[24], "g")
        # 25 hidden merge_key = item_uuid stringified
        self.assertEqual(row[25], "ITEM_X")

    def test_tail_slots_contain_cancelled_metrics(self):
        row = _build_rows(make_agg(bom=[("M1", "x", 1.0, 1.0, "g")]), mode="single")[0]
        # 26-29 + 30-31 are formula placeholders (None); 32/33 carry cancelled values.
        self.assertIsNone(row[26])
        self.assertIsNone(row[29])
        self.assertIsNone(row[30])
        self.assertIsNone(row[31])
        self.assertEqual(row[32], 3.0)
        self.assertEqual(row[33], 30.0)

    def test_no_bom_row_uses_dash_placeholders(self):
        row = _build_rows(make_agg(bom=[]), mode="single")[0]
        self.assertEqual(row[20], "-")   # BOM name
        self.assertEqual(row[21], "-")   # BOM code
        self.assertIsNone(row[22])
        self.assertIsNone(row[23])
        self.assertEqual(row[24], "-")

    def test_multiple_bom_lines_share_item_block(self):
        """Multi-BOM item → multiple rows sharing identical merge-key fields (cols 0-19,25,32-33)."""
        agg = make_agg(bom=[
            ("M1", "盐", 1.0, 0.5, "g"),
            ("M2", "糖", 2.0, 0.7, "g"),
        ])
        rows = _build_rows(agg, mode="single")
        self.assertEqual(len(rows), 2)
        # Block-shared columns identical:
        for shared_col in (0, 1, 2, 3, 4, 5, 6, 7, 25, 32, 33):
            self.assertEqual(rows[0][shared_col], rows[1][shared_col])
        # BOM columns differ:
        self.assertEqual([rows[0][21], rows[1][21]], ["M1", "M2"])


class FallbackBomEnrichment(unittest.TestCase):
    """When agg has no BOM, fallback BOMs may fill in rows via name match."""

    def test_fallback_match_emits_bom_rows(self):
        agg = make_agg(bom=[])
        fallback = {"商品A": [("M9", "原料", 5.0, "g")]}
        # Uploaded price provides the unit price.
        rows = _build_rows(agg, mode="single",
                           fallback_boms=fallback, uploaded_prices={"M9": 1.5})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][20], "原料")
        self.assertEqual(rows[0][21], "M9")
        self.assertEqual(rows[0][22], 5.0)
        self.assertEqual(rows[0][23], 1.5)

    def test_no_fallback_match_keeps_dash_row(self):
        rows = _build_rows(make_agg(bom=[]), mode="single",
                           fallback_boms={"其他商品": [("M9", "x", 1.0, "g")]})
        self.assertEqual(rows[0][21], "-")


class YamlIndexCrossCheck(unittest.TestCase):
    """Cross-check: every field_index referenced in profit_margin.yaml must fit
    inside the row length produced by _build_rows. Catches drift between yaml
    and Python when either side changes."""

    def test_all_yaml_field_indices_within_row(self):
        import yaml
        from pathlib import Path

        from tests._setup import REPO_ROOT
        yaml_path = REPO_ROOT / "resources/reports/profit_margin.yaml"
        config = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

        # Build a real row to know its true length.
        sample = _build_rows(make_agg(bom=[("M1", "x", 1.0, 1.0, "g")]), mode="single")[0]
        row_len = len(sample)

        for sheet_name, sheet in config["sheets"].items():
            for col in sheet["columns"]:
                idx = col.get("field_index", 0)
                self.assertLess(
                    idx, row_len,
                    f"yaml '{sheet_name}.{col['name']}' field_index={idx} "
                    f"out of bounds (row len {row_len})",
                )


if __name__ == "__main__":
    unittest.main()
