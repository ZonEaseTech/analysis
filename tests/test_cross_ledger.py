"""跨账本互证: 行构建器 + CROSS_LEDGER 恒等式 (spec §5 A1)。"""
import unittest

import tests._setup  # noqa: F401

from semantic.validators import check
from semantic.validators.core import Severity
from semantic.validators.cross_ledger import build_cross_ledger_rows
from semantic.validators.identities import CROSS_LEDGER_IDENTITIES


def _stat(store="001", item="100", qty=10.0, gross=100.0):
    return {"store_num": store, "item_uuid": item, "item_name": "商品A",
            "qty": qty, "gross_amount": gross}


def _voucher(store="001", item="100", qty=10.0, gross=100.0):
    return {"store_num": store, "item_uuid": item,
            "voucher_qty": qty, "voucher_gross": gross}


class TestBuildCrossLedgerRows(unittest.TestCase):
    def test_matched_pair_merges(self):
        rows = build_cross_ledger_rows([_stat()], [_voucher()])
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["stat_qty"], 10.0)
        self.assertEqual(r["voucher_qty"], 10.0)
        self.assertEqual(r["voucher_present"], 1.0)

    def test_missing_voucher_flagged_not_zero_faked(self):
        rows = build_cross_ledger_rows([_stat()], [])
        self.assertEqual(rows[0]["voucher_present"], 0.0)

    def test_keyed_by_store_and_item(self):
        rows = build_cross_ledger_rows(
            [_stat(store="001"), _stat(store="002", qty=5.0)],
            [_voucher(store="001"), _voucher(store="002", qty=5.0)])
        self.assertEqual(len(rows), 2)
        by_store = {r["store_num"]: r for r in rows}
        self.assertEqual(by_store["002"]["voucher_qty"], 5.0)


class TestCrossLedgerIdentities(unittest.TestCase):
    def test_balanced_rows_pass(self):
        rows = build_cross_ledger_rows([_stat()], [_voucher()])
        result = check(rows, CROSS_LEDGER_IDENTITIES)
        self.assertEqual(result.violations, [])

    def test_qty_drift_is_must_fix(self):
        rows = build_cross_ledger_rows([_stat(qty=10.0)], [_voucher(qty=9.0)])
        result = check(rows, CROSS_LEDGER_IDENTITIES)
        self.assertTrue(any(v.severity == Severity.MUST_FIX
                            and v.identity.name == "跨账本销量互证"
                            for v in result.violations))

    def test_missing_voucher_is_must_fix_coverage(self):
        rows = build_cross_ledger_rows([_stat(qty=10.0)], [])
        result = check(rows, CROSS_LEDGER_IDENTITIES)
        names = {v.identity.name for v in result.violations
                 if v.severity == Severity.MUST_FIX}
        self.assertIn("凭证账覆盖完整性", names)

    def test_zero_qty_without_voucher_ok(self):
        # 统计账没量, 凭证账没行 — 不算缺
        rows = build_cross_ledger_rows([_stat(qty=0.0, gross=0.0)], [])
        result = check(rows, CROSS_LEDGER_IDENTITIES)
        self.assertEqual([v for v in result.violations
                          if v.identity.name == "凭证账覆盖完整性"], [])


if __name__ == "__main__":
    unittest.main()
