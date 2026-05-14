"""P4 — PlatformPayoutCheck 测试 (Phase 1 框架).

跑法: venv/bin/python -m unittest tests.test_platform_payout -v
"""
from __future__ import annotations

import unittest

from semantic.reconciliation import (
    PlatformPayoutCheck,
    PlatformPayoutRecord,
    ReconciliationSeverity,
    load_grab_statement,
    load_lineman_statement,
    load_shopee_statement,
)


class PayoutRecordTests(unittest.TestCase):
    def test_immutable(self):
        r = PlatformPayoutRecord(
            platform="grab", period="2026-04", store_id="s1",
            gross_sales=100000, commission=30000,
        )
        with self.assertRaises(Exception):
            r.gross_sales = 999  # noqa


class PayoutCheckTests(unittest.TestCase):
    def _record(self, store_id, gross, platform="grab"):
        return PlatformPayoutRecord(
            platform=platform, period="2026-04",
            store_id=store_id, gross_sales=gross,
            commission=gross * 0.30, adjustments=0,
            net_payout=gross * 0.70,
            raw_source="grab_202604.xlsx",
        )

    def test_perfect_match_no_discrepancy(self):
        records = [self._record("s1", 100_000), self._record("s2", 80_000)]
        ck = PlatformPayoutCheck(
            name="test",
            bq_by_store={"s1": 100_000, "s2": 80_000},
            payout_records=records,
        )
        result = ck.run()
        self.assertEqual(result.severity, ReconciliationSeverity.NEGLIGIBLE)
        self.assertEqual(len(result.discrepancies), 0)
        self.assertEqual(result.total_compared, 2)

    def test_under_05pct_review(self):
        """0.1-0.5% 差 → NEEDS_REVIEW."""
        ck = PlatformPayoutCheck(
            name="test",
            bq_by_store={"s1": 100_300},  # 比对账单多 300, 0.3%
            payout_records=[self._record("s1", 100_000)],
        )
        result = ck.run()
        self.assertEqual(result.severity, ReconciliationSeverity.NEEDS_REVIEW)

    def test_over_05pct_must_fix(self):
        """> 0.5% 差 → MUST_FIX."""
        ck = PlatformPayoutCheck(
            name="test",
            bq_by_store={"s1": 105_000},  # 5% 差
            payout_records=[self._record("s1", 100_000)],
        )
        result = ck.run()
        self.assertEqual(result.severity, ReconciliationSeverity.MUST_FIX)
        d = result.discrepancies[0]
        self.assertIn("grab/s1/2026-04", d.entity_id)
        self.assertIn("commission", d.note)
        self.assertIn("net_payout", d.note)

    def test_platform_filter(self):
        """platform_filter 只跑指定平台."""
        records = [
            self._record("s1", 100_000, platform="grab"),
            self._record("s2", 80_000, platform="lineman"),
        ]
        ck = PlatformPayoutCheck(
            name="test", bq_by_store={"s1": 95_000, "s2": 79_000},
            payout_records=records, platform_filter="grab",
        )
        result = ck.run()
        self.assertEqual(result.total_compared, 1)  # 只跑 grab

    def test_missing_store_in_bq(self):
        """BQ 没该店 (bq_by_store 缺) → bq_value = 0, 差全部 gross_sales 必查."""
        ck = PlatformPayoutCheck(
            name="test",
            bq_by_store={},  # 没 s1
            payout_records=[self._record("s1", 100_000)],
        )
        result = ck.run()
        self.assertEqual(result.severity, ReconciliationSeverity.MUST_FIX)


class LoaderStubTests(unittest.TestCase):
    """加载器目前是 NotImplementedError 占位 (Phase 2 实施)."""

    def test_grab_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            load_grab_statement("/tmp/dummy.xlsx")

    def test_lineman_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            load_lineman_statement("/tmp/dummy.xlsx")

    def test_shopee_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            load_shopee_statement("/tmp/dummy.xlsx")


if __name__ == "__main__":
    unittest.main()
