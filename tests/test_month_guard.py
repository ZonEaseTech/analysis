"""封存线: 2026-05 及之前为旧浮点口径封存月, 交付物禁止重新导出 (spec §2 决策 2/3)。"""
import unittest

import tests._setup  # noqa: F401

from semantic.dimensions.time import FROZEN_BEFORE_MONTH, assert_month_not_frozen


class TestMonthGuard(unittest.TestCase):
    def test_frozen_month_blocks(self):
        with self.assertRaises(SystemExit) as cm:
            assert_month_not_frozen("2026-05")
        self.assertEqual(cm.exception.code, 3)

    def test_old_month_blocks(self):
        with self.assertRaises(SystemExit):
            assert_month_not_frozen("2025-12")

    def test_current_month_passes(self):
        assert_month_not_frozen("2026-06")  # 不抛即过

    def test_future_month_passes(self):
        assert_month_not_frozen("2027-01")

    def test_constant_value(self):
        self.assertEqual(FROZEN_BEFORE_MONTH, "2026-06")


if __name__ == "__main__":
    unittest.main()
