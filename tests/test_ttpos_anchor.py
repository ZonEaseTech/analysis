"""P4 — TtposAnchorCheck 测试。

跑法: venv/bin/python -m unittest tests.test_ttpos_anchor -v
"""
from __future__ import annotations

import unittest

from semantic.reconciliation import (
    ReconciliationSeverity,
    TtposAnchorCheck,
)


class TtposAnchorTests(unittest.TestCase):
    def test_exact_match(self):
        ck = TtposAnchorCheck(
            name="test", bq_net_sales=1_000_000, ttpos_net_sales=1_000_000,
        )
        result = ck.run()
        self.assertEqual(result.severity, ReconciliationSeverity.NEGLIGIBLE)
        self.assertEqual(result.discrepancies, [])
        self.assertIn("delta +0.00", result.summary)

    def test_negligible_diff_under_1_thb(self):
        """< 1 元 abs (浮点累积) 应该 NEGLIGIBLE."""
        ck = TtposAnchorCheck(
            name="test", bq_net_sales=1_000_000, ttpos_net_sales=1_000_000.49,
        )
        result = ck.run()
        self.assertEqual(result.severity, ReconciliationSeverity.NEGLIGIBLE)

    def test_needs_review_around_0_01pct(self):
        """100 元 / 0.01% 的差 → NEEDS_REVIEW."""
        # 1M base, 200 abs = 0.02%
        ck = TtposAnchorCheck(
            name="test", bq_net_sales=1_000_200, ttpos_net_sales=1_000_000,
        )
        result = ck.run()
        self.assertEqual(result.severity, ReconciliationSeverity.NEEDS_REVIEW)
        self.assertEqual(len(result.discrepancies), 1)
        self.assertIn("BQ 1,000,200", result.summary)
        self.assertIn("ttpos 1,000,000", result.summary)

    def test_must_fix_above_0_1pct(self):
        """> 0.1% diff → MUST_FIX."""
        # 0.5% 差
        ck = TtposAnchorCheck(
            name="test", bq_net_sales=1_005_000, ttpos_net_sales=1_000_000,
        )
        result = ck.run()
        self.assertEqual(result.severity, ReconciliationSeverity.MUST_FIX)
        d = result.discrepancies[0]
        self.assertEqual(d.bq_value, 1_005_000)
        self.assertEqual(d.external_value, 1_000_000)
        self.assertIn("漂移", d.note)

    def test_negative_delta(self):
        """BQ 比 ttpos 低 (delta < 0)."""
        ck = TtposAnchorCheck(
            name="test", bq_net_sales=995_000, ttpos_net_sales=1_000_000,
        )
        result = ck.run()
        self.assertEqual(result.severity, ReconciliationSeverity.MUST_FIX)
        self.assertIn("delta -5,000.00", result.summary)

    def test_zero_ttpos_safe(self):
        """ttpos = 0 时不该崩 (空店 / 没数据)."""
        ck = TtposAnchorCheck(
            name="test", bq_net_sales=0, ttpos_net_sales=0,
        )
        result = ck.run()
        self.assertEqual(result.severity, ReconciliationSeverity.NEGLIGIBLE)


if __name__ == "__main__":
    unittest.main()
