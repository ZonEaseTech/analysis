"""Price priority chain: uploaded > ERPNext > BQ default.

Also covers BOM_UNIT_CORRECTIONS (per-material divisor applied to ERP price only).
"""
import unittest

from tests import _setup  # noqa: F401

from bq_reports.profit_margin_report import (
    BOM_UNIT_CORRECTIONS,
    _resolve_base_unit_price,
)


class PriorityChainTests(unittest.TestCase):
    def test_empty_material_code_returns_bq_price(self):
        self.assertEqual(_resolve_base_unit_price("", 1.5, {"X": 9}, {"X": (9, "kg")}), 1.5)
        self.assertEqual(_resolve_base_unit_price(None, 2.0, {"X": 9}, {}), 2.0)

    def test_uploaded_beats_erpnext_and_bq(self):
        price = _resolve_base_unit_price(
            "M001", bq_price=100, uploaded_prices={"M001": 1.23},
            erp_prices={"M001": (99, "kg")},
        )
        self.assertEqual(price, 1.23)

    def test_erpnext_used_when_uploaded_missing(self):
        price = _resolve_base_unit_price(
            "M002", bq_price=100, uploaded_prices={"OTHER": 1.0},
            erp_prices={"M002": (5.5, "kg")},
        )
        self.assertEqual(price, 5.5)

    def test_bq_default_used_when_both_missing(self):
        self.assertEqual(
            _resolve_base_unit_price("M003", 7.7, {}, {}),
            7.7,
        )

    def test_case_insensitive_lookup_for_uploaded(self):
        price = _resolve_base_unit_price(
            "fr01008", bq_price=0,
            uploaded_prices={"FR01008": 2.5}, erp_prices={},
        )
        self.assertEqual(price, 2.5)

    def test_case_insensitive_lookup_for_erp(self):
        price = _resolve_base_unit_price(
            "fr01008", bq_price=0,
            uploaded_prices={}, erp_prices={"FR01008": (3.0, "kg")},
        )
        self.assertEqual(price, 3.0)


class UnitCorrectionTests(unittest.TestCase):
    """BOM_UNIT_CORRECTIONS divides the ERP price — not the uploaded price.

    Rationale (locked here): uploaded sheet is already in the recipe unit (we
    divided by 销售换算系数 at load time), but ERP price is per inventory unit.

    过渡期 hack (Task 3.1): 此修正在 UOM 校验通过后才应用, 待 desired-UOM 校验
    接进生产路径 (Phase 5 / Task #6) 后退役 (见 Task 4.1 anchor)。
    """

    def test_correction_applied_only_to_erp(self):
        self.assertIn("MK01018", BOM_UNIT_CORRECTIONS)
        divisor = BOM_UNIT_CORRECTIONS["MK01018"]
        price = _resolve_base_unit_price(
            "MK01018", bq_price=0, uploaded_prices={},
            erp_prices={"MK01018": (divisor * 10, "pack")},
        )
        self.assertEqual(price, 10.0, "ERP price should be divided by correction factor")

    def test_correction_not_applied_when_uploaded_wins(self):
        # Uploaded price short-circuits the chain; correction must not run.
        price = _resolve_base_unit_price(
            "MK01018", bq_price=0,
            uploaded_prices={"MK01018": 7.0},
            erp_prices={"MK01018": (500, "pack")},  # would yield 10 if corrected
        )
        self.assertEqual(price, 7.0)

    def test_no_correction_for_unlisted_material(self):
        price = _resolve_base_unit_price(
            "XYZ999", bq_price=0, uploaded_prices={},
            erp_prices={"XYZ999": (8.0, "kg")},
        )
        self.assertEqual(price, 8.0)


if __name__ == "__main__":
    unittest.main()
