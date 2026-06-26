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
    """BOM_UNIT_CORRECTIONS 硬编码已退役 (Task 3.1).

    原 ÷50 修正 (MK01018) 是针对 ERP 单位与 BOM 消耗单位不一致的临时 hack;
    退役后正确做法是 ERP 在消耗 UOM 上维护 Item Price,
    或通过 desired_uoms 校验触发缺口报警 (见 Task 4.1 anchor)。
    BOM_UNIT_CORRECTIONS 保留为空 dict 以维持向后兼容。
    """

    def test_bom_unit_corrections_is_empty(self):
        # 硬编码已退役; 空 dict 保持 backward-compat (import 不 NameError)
        self.assertIsInstance(BOM_UNIT_CORRECTIONS, dict)
        self.assertEqual(len(BOM_UNIT_CORRECTIONS), 0)

    def test_erp_price_returned_without_correction(self):
        # MK01018 ERP 价直接返回原值, 不再 ÷50
        price = _resolve_base_unit_price(
            "MK01018", bq_price=0, uploaded_prices={},
            erp_prices={"MK01018": (500, "pack")},
        )
        self.assertEqual(price, 500, "ERP price should NOT be divided after correction retired")

    def test_uploaded_still_beats_erp(self):
        # uploaded priority 80 > ERPNext priority 50, 与修正退役无关
        price = _resolve_base_unit_price(
            "MK01018", bq_price=0,
            uploaded_prices={"MK01018": 7.0},
            erp_prices={"MK01018": (500, "pack")},
        )
        self.assertEqual(price, 7.0)

    def test_no_correction_for_any_material(self):
        # 所有物料均无硬编码修正
        price = _resolve_base_unit_price(
            "XYZ999", bq_price=0, uploaded_prices={},
            erp_prices={"XYZ999": (8.0, "kg")},
        )
        self.assertEqual(price, 8.0)


if __name__ == "__main__":
    unittest.main()
