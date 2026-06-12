"""classify_money_severity 的边界测试 — 重点钉死 base=0 漏洞 (spec §1 问题 5)。"""
import unittest

import tests._setup  # noqa: F401

from semantic.reconciliation.base import (
    ReconciliationSeverity,
    classify_money_severity,
)


class TestClassifyMoneySeverity(unittest.TestCase):
    def test_zero_base_large_delta_must_fix(self):
        """external=0 但 BQ 侧 10000 — 修复前被 or 分支放过成 NEGLIGIBLE."""
        sev = classify_money_severity(10000.0, 0.0)
        self.assertEqual(sev, ReconciliationSeverity.MUST_FIX)

    def test_zero_base_tiny_delta_negligible(self):
        sev = classify_money_severity(0.5, 0.0)
        self.assertEqual(sev, ReconciliationSeverity.NEGLIGIBLE)

    def test_small_abs_and_small_rel_negligible(self):
        # abs<1 且 rel 极小 → NEGLIGIBLE (合法的累积舍入)
        sev = classify_money_severity(0.5, 1_000_000.0)
        self.assertEqual(sev, ReconciliationSeverity.NEGLIGIBLE)

    def test_small_rel_but_large_abs_not_negligible(self):
        """rel<0.0001 但绝对差 500 元 — 修复前被 rel 分支放过."""
        sev = classify_money_severity(500.0, 10_000_000.0)
        # rel=0.00005 < fatal_rel → 不是 MUST_FIX; 钉死精确档位防二阶回归
        self.assertEqual(sev, ReconciliationSeverity.NEEDS_REVIEW)

    def test_fatal_rel(self):
        sev = classify_money_severity(200.0, 1000.0)  # rel=0.2 > 0.01
        self.assertEqual(sev, ReconciliationSeverity.MUST_FIX)


if __name__ == "__main__":
    unittest.main()
