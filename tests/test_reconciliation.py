"""P4 — Reconciliation kernel + InternalConsistencyCheck 测试。

跑法: venv/bin/python -m unittest tests.test_reconciliation -v
"""
from __future__ import annotations

import unittest

from semantic.reconciliation import (
    Discrepancy,
    InternalConsistencyCheck,
    ReconciliationCheck,
    ReconciliationResult,
    ReconciliationSeverity,
    run_checks,
)
from semantic.reconciliation.base import classify_money_severity


# ═══════════════════════════════════════════════════════════════════
# Discrepancy dataclass
# ═══════════════════════════════════════════════════════════════════

class DiscrepancyTests(unittest.TestCase):
    def test_delta(self):
        d = Discrepancy(entity_id="x", bq_value=120, external_value=100)
        self.assertEqual(d.delta, 20)

    def test_relative_delta(self):
        d = Discrepancy(entity_id="x", bq_value=120, external_value=100)
        self.assertAlmostEqual(d.relative_delta, 0.20)

    def test_relative_delta_zero_external(self):
        d = Discrepancy(entity_id="x", bq_value=120, external_value=0)
        self.assertIsNone(d.relative_delta)

    def test_negative_external(self):
        d = Discrepancy(entity_id="x", bq_value=-50, external_value=-100)
        self.assertEqual(d.delta, 50)  # 亏损减少了 50
        self.assertAlmostEqual(d.relative_delta, 0.50)

    def test_immutable(self):
        d = Discrepancy(entity_id="x", bq_value=1, external_value=2)
        with self.assertRaises(Exception):
            d.bq_value = 99  # noqa


# ═══════════════════════════════════════════════════════════════════
# ReconciliationResult
# ═══════════════════════════════════════════════════════════════════

class ResultTests(unittest.TestCase):
    def test_matched_count(self):
        r = ReconciliationResult(
            check_name="x", total_compared=100,
            discrepancies=[
                Discrepancy(entity_id="a", bq_value=1, external_value=1)
                for _ in range(5)
            ],
        )
        self.assertEqual(r.matched, 95)

    def test_has_must_fix(self):
        r = ReconciliationResult(
            check_name="x", total_compared=10,
            severity=ReconciliationSeverity.MUST_FIX,
        )
        self.assertTrue(r.has_must_fix)

        r2 = ReconciliationResult(
            check_name="x", total_compared=10,
            severity=ReconciliationSeverity.NEEDS_REVIEW,
        )
        self.assertFalse(r2.has_must_fix)


# ═══════════════════════════════════════════════════════════════════
# classify_money_severity
# ═══════════════════════════════════════════════════════════════════

class MoneySeverityTests(unittest.TestCase):
    def test_negligible_small_abs(self):
        # |delta| < 1 元
        sev = classify_money_severity(abs_delta=0.5, base=10000)
        self.assertEqual(sev, ReconciliationSeverity.NEGLIGIBLE)

    def test_negligible_small_rel(self):
        # rel < 0.01% (1 万分之一)
        sev = classify_money_severity(abs_delta=5, base=100000)
        self.assertEqual(sev, ReconciliationSeverity.NEGLIGIBLE)

    def test_review_in_band(self):
        # abs=500 (>100), base=100000 → rel=0.5% (< 1% fatal, > 0.1% review_rel)
        sev = classify_money_severity(abs_delta=500, base=100000)
        self.assertEqual(sev, ReconciliationSeverity.NEEDS_REVIEW)

    def test_must_fix_high_rel(self):
        # rel = 5% > 1% fatal_rel
        sev = classify_money_severity(abs_delta=5000, base=100000)
        self.assertEqual(sev, ReconciliationSeverity.MUST_FIX)

    def test_zero_base(self):
        # base = 0 时 rel 无意义，走纯绝对值判断
        sev = classify_money_severity(abs_delta=10, base=0)
        # |abs|=10 > 1 (negligible_abs) 且 < 100 (review_abs) → NEEDS_REVIEW
        # 旧版错误地走 rel 路径，rel=0 < negligible_rel 被判 NEGLIGIBLE (spec §1 问题 5)
        # 修复后必须走 base=0 分支，返回 NEEDS_REVIEW
        self.assertEqual(sev, ReconciliationSeverity.NEEDS_REVIEW)


# ═══════════════════════════════════════════════════════════════════
# InternalConsistencyCheck (integration with validator)
# ═══════════════════════════════════════════════════════════════════

class InternalConsistencyTests(unittest.TestCase):
    def _make_good_row(self):
        """完全 OK 的 row, 不触发任何 identity."""
        return {
            "store_num": "001", "item_name": "OK_item",
            "qty": 100, "net_qty": 100, "free_qty": 0, "give_qty": 0,
            "refund_qty": 0, "cancelled_qty": 0,
            "sales_price": 1000, "revenue": 1000,
            "refund_amount": 0, "free_amount": 0, "give_amount": 0,
            "discount_amount": 0, "cancelled_amount": 0,
        }

    def test_clean_rows_no_discrepancies(self):
        check = InternalConsistencyCheck(
            name="test_clean",
            rows=[self._make_good_row()],
        )
        result = check.run()
        self.assertEqual(result.severity, ReconciliationSeverity.NEGLIGIBLE)
        self.assertEqual(len(result.discrepancies), 0)

    def test_broken_amount_identity_must_fix(self):
        """金额恒等式破: sales=1000 但 sum(actual+losses) != 1000."""
        row = self._make_good_row()
        row["revenue"] = 500  # 应该 1000
        # delta = 500; > 100 abs + > 5% rel → MUST_FIX
        check = InternalConsistencyCheck(name="test_break", rows=[row])
        result = check.run()
        self.assertEqual(result.severity, ReconciliationSeverity.MUST_FIX)
        self.assertGreater(len(result.discrepancies), 0)
        # discrepancy 提示有 identity 信息
        d = result.discrepancies[0]
        self.assertIn("identity=", d.note)

    def test_source_coverage_violation(self):
        """有销量但 bom_source='无' → MUST_FIX."""
        row = self._make_good_row()
        row["bom_source"] = "无"
        check = InternalConsistencyCheck(name="test_source", rows=[row])
        result = check.run()
        self.assertEqual(result.severity, ReconciliationSeverity.MUST_FIX)

    def test_sanity_band_review(self):
        """退款率 10% → NEEDS_REVIEW."""
        row = self._make_good_row()
        row["refund_qty"] = 10  # 10%
        # 但 qty/sales 等也要相应调整, 不然金额恒等式破 → 同时 must_fix
        # 简化: 只看 sanity band 单独表现
        # 注意: 金额恒等式可能也会 fail 因为退款没体现在 amount 里.
        # 让 sanity band 单跑:
        from semantic.validators.identities import SANITY_BAND_IDENTITIES
        check = InternalConsistencyCheck(
            name="test_sanity",
            rows=[row],
            identities=SANITY_BAND_IDENTITIES,
        )
        result = check.run()
        # 退款率 10% 是 NEEDS_REVIEW (0-5% OK; 5-20% review)
        self.assertEqual(result.severity, ReconciliationSeverity.NEEDS_REVIEW)

    def test_custom_row_label(self):
        row = self._make_good_row()
        row["revenue"] = 500  # 触发金额恒等式
        check = InternalConsistencyCheck(
            name="test", rows=[row],
            row_label=lambda r: f"CUSTOM_{r['store_num']}",
        )
        result = check.run()
        self.assertGreater(len(result.discrepancies), 0)
        self.assertIn("CUSTOM_001", result.discrepancies[0].entity_id)


# ═══════════════════════════════════════════════════════════════════
# run_checks (批量)
# ═══════════════════════════════════════════════════════════════════

class _DummyCheck:
    """自定义 Check (验证 Protocol)."""
    def __init__(self, name, severity):
        self.name = name
        self._severity = severity

    def run(self):
        return ReconciliationResult(
            check_name=self.name, total_compared=10, severity=self._severity,
        )


class RunChecksTests(unittest.TestCase):
    def test_runs_all_checks(self):
        checks = [
            _DummyCheck("c1", ReconciliationSeverity.NEGLIGIBLE),
            _DummyCheck("c2", ReconciliationSeverity.NEEDS_REVIEW),
            _DummyCheck("c3", ReconciliationSeverity.MUST_FIX),
        ]
        results = run_checks(checks)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].check_name, "c1")
        self.assertEqual(results[2].severity, ReconciliationSeverity.MUST_FIX)

    def test_progress_callback(self):
        progress = []

        def on_progress(name):
            progress.append(name)

        checks = [_DummyCheck("a", ReconciliationSeverity.NEGLIGIBLE),
                  _DummyCheck("b", ReconciliationSeverity.NEGLIGIBLE)]
        run_checks(checks, on_progress=on_progress)
        self.assertEqual(progress, ["a", "b"])

    def test_protocol_compatible(self):
        """duck typing: _DummyCheck 是 ReconciliationCheck 实例."""
        ck = _DummyCheck("x", ReconciliationSeverity.NEGLIGIBLE)
        self.assertIsInstance(ck, ReconciliationCheck)


if __name__ == "__main__":
    unittest.main()
