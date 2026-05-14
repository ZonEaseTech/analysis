"""P3.5a — semantic/comparison/period_compare.py 测试.

跑法: venv/bin/python -m unittest tests.test_period_compare -v
"""
from __future__ import annotations

import unittest

from semantic.comparison import (
    PeriodChange,
    compute_mom_changes,
    compute_period_change,
    compute_yoy_changes,
    format_pct_delta,
)


# ═══════════════════════════════════════════════════════════════════
# compute_period_change (单值)
# ═══════════════════════════════════════════════════════════════════

class PeriodChangeTests(unittest.TestCase):
    def test_normal_increase(self):
        c = compute_period_change(120, 100)
        self.assertEqual(c.current, 120)
        self.assertEqual(c.previous, 100)
        self.assertEqual(c.abs_delta, 20)
        self.assertAlmostEqual(c.pct_delta, 0.20)
        self.assertTrue(c.is_improvement)

    def test_normal_decrease(self):
        c = compute_period_change(80, 100)
        self.assertEqual(c.abs_delta, -20)
        self.assertAlmostEqual(c.pct_delta, -0.20)
        self.assertFalse(c.is_improvement)

    def test_zero_previous_nonzero_current(self):
        """previous = 0, current 非 0 → pct 无穷大, 设 None."""
        c = compute_period_change(50, 0)
        self.assertEqual(c.abs_delta, 50)
        self.assertIsNone(c.pct_delta)

    def test_both_zero(self):
        c = compute_period_change(0, 0)
        self.assertEqual(c.abs_delta, 0)
        self.assertEqual(c.pct_delta, 0.0)

    def test_none_previous(self):
        """无上一期数据."""
        c = compute_period_change(100, None)
        self.assertEqual(c.current, 100)
        self.assertIsNone(c.previous)
        self.assertIsNone(c.abs_delta)
        self.assertIsNone(c.pct_delta)

    def test_negative_previous(self):
        """previous 是负数 (亏损), pct 用 abs(prev)."""
        c = compute_period_change(-50, -100)
        # 亏损减少 50; abs_delta=+50, pct = 50/100 = 0.5
        self.assertEqual(c.abs_delta, 50)
        self.assertAlmostEqual(c.pct_delta, 0.50)

    def test_pp_delta_for_percentages(self):
        """两期都是百分比时, pp_delta = abs_delta."""
        c = compute_period_change(0.65, 0.60)
        self.assertAlmostEqual(c.pp_delta, 0.05)  # 5pp 上升


class PeriodChangeImmutabilityTests(unittest.TestCase):
    def test_frozen(self):
        c = PeriodChange(current=100, previous=80,
                        abs_delta=20, pct_delta=0.25)
        with self.assertRaises(Exception):
            c.current = 200  # noqa


# ═══════════════════════════════════════════════════════════════════
# compute_mom_changes (字典级)
# ═══════════════════════════════════════════════════════════════════

class MomChangesTests(unittest.TestCase):
    def test_dict_compare(self):
        cur = {"gmv": 120, "net_sales": 100}
        prev = {"gmv": 100, "net_sales": 90}
        out = compute_mom_changes(cur, prev)
        self.assertAlmostEqual(out["gmv"].pct_delta, 0.20)
        self.assertAlmostEqual(out["net_sales"].pct_delta, 100/90 - 1)

    def test_missing_previous_key(self):
        """key 在 cur 有但 prev 没有 → PeriodChange.previous = None."""
        cur = {"gmv": 100, "new_metric": 50}
        prev = {"gmv": 80}
        out = compute_mom_changes(cur, prev)
        self.assertIsNone(out["new_metric"].previous)
        self.assertIsNone(out["new_metric"].pct_delta)

    def test_none_previous_dict(self):
        cur = {"gmv": 100}
        out = compute_mom_changes(cur, None)
        self.assertIsNone(out["gmv"].previous)


# ═══════════════════════════════════════════════════════════════════
# compute_yoy_changes (跟 mom 同实现, 不同语义)
# ═══════════════════════════════════════════════════════════════════

class YoyChangesTests(unittest.TestCase):
    def test_yoy_delegates_to_mom(self):
        """YoY 实现跟 MoM 一样, 只是 caller 传去年同月数据."""
        cur = {"gmv": 1100}
        year_ago = {"gmv": 1000}
        out = compute_yoy_changes(cur, year_ago)
        self.assertAlmostEqual(out["gmv"].pct_delta, 0.10)

    def test_yoy_no_data_returns_none(self):
        """数据未满 12 个月 → year_ago_amounts=None → 全部 N/A."""
        cur = {"gmv": 1000, "net_sales": 800}
        out = compute_yoy_changes(cur, None)
        for code in cur:
            self.assertIsNone(out[code].previous)
            self.assertIsNone(out[code].pct_delta)


# ═══════════════════════════════════════════════════════════════════
# format_pct_delta (展示用)
# ═══════════════════════════════════════════════════════════════════

class FormatPctDeltaTests(unittest.TestCase):
    def test_positive_with_sign(self):
        self.assertEqual(format_pct_delta(0.083), "+8.3%")

    def test_negative_with_sign(self):
        self.assertEqual(format_pct_delta(-0.15), "-15.0%")

    def test_zero(self):
        self.assertEqual(format_pct_delta(0), "—")

    def test_none_na(self):
        self.assertEqual(format_pct_delta(None), "N/A")

    def test_custom_precision(self):
        self.assertEqual(format_pct_delta(0.08333, precision=2), "+8.33%")


if __name__ == "__main__":
    unittest.main()
