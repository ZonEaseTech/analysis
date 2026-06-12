"""Validator framework — Identity, Severity, check(), classification.

Pins down:
  - Three-tier Severity matches Soda's pass/warn/fail
  - Per-identity classify() function controls thresholds
  - check() collects violations flat (easy to filter)
  - Money classifier honours both absolute & relative bands
  - Qty classifier has zero tolerance (any drift = MUST_FIX)
"""
import unittest

from tests import _setup  # noqa: F401

from semantic.validators import Severity, check
from semantic.validators.core import Identity, Result
from semantic.validators.identities import (
    AMOUNT_IDENTITY,
    DEFAULT_IDENTITIES,
    SALES_QTY_IDENTITY,
    _money_classify,
    _qty_classify,
)


def clean_row(**overrides):
    """A row that satisfies all built-in identities exactly."""
    row = dict(
        qty=10, net_qty=8, free_qty=1, give_qty=1, refund_qty=0, cancelled_qty=0,
        sales_price=100.0, revenue=80.0, refund_amount=0,
        free_amount=10, give_amount=10, cancelled_amount=0, discount_amount=0,
        gross_amount=100.0,  # = sales_price(100) + cancelled_amount(0)
    )
    row.update(overrides)
    return row


class QtyClassifierTests(unittest.TestCase):
    """Integer accounting — zero is the only acceptable delta."""

    def test_zero_is_negligible(self):
        self.assertEqual(_qty_classify(0, 10), Severity.NEGLIGIBLE)
        self.assertEqual(_qty_classify(0.0, 10), Severity.NEGLIGIBLE)

    def test_any_nonzero_is_must_fix(self):
        self.assertEqual(_qty_classify(1, 100), Severity.MUST_FIX)
        self.assertEqual(_qty_classify(-1, 100), Severity.MUST_FIX)
        # No "small qty drift is OK" — integers must balance.
        self.assertEqual(_qty_classify(0.001, 100), Severity.MUST_FIX)


class MoneyClassifierTests(unittest.TestCase):
    """Two-axis: absolute OR relative threshold determines severity."""

    def test_sub_cent_negligible(self):
        self.assertEqual(_money_classify(0.005, 1000), Severity.NEGLIGIBLE)

    def test_rounding_accumulation_negligible(self):
        # delta < 1 AND rel < 0.1% → NEGLIGIBLE
        self.assertEqual(_money_classify(0.5, 10000), Severity.NEGLIGIBLE)

    def test_small_but_not_rounding_needs_review(self):
        # delta ~ 5 元, rel = 5% — boundary case; 5% is *not* > 5% so REVIEW
        self.assertEqual(_money_classify(5.0, 100.0), Severity.NEEDS_REVIEW)

    def test_large_absolute_must_fix(self):
        # 101 元 > 100 threshold
        self.assertEqual(_money_classify(101, 100_000), Severity.MUST_FIX)

    def test_large_relative_must_fix(self):
        # delta 10 元, lhs 100 → 10% > 5% threshold
        self.assertEqual(_money_classify(10, 100), Severity.MUST_FIX)

    def test_zero_lhs_does_not_explode(self):
        # 比例除零必须用 lhs=0 安全分支
        sev = _money_classify(50, 0)
        self.assertEqual(sev, Severity.NEEDS_REVIEW)


class CheckFunctionTests(unittest.TestCase):
    def test_clean_row_no_violations(self):
        res = check([clean_row()], DEFAULT_IDENTITIES)
        self.assertEqual(res.violations, [])
        self.assertFalse(res.has_must_fix)

    def test_qty_drift_one_violation(self):
        # qty=10 但 net+free+give+refund+cancel = 9 → MUST_FIX
        res = check([clean_row(net_qty=7)], DEFAULT_IDENTITIES)
        qty_viols = res.by_identity("销量恒等式")
        self.assertEqual(len(qty_viols), 1)
        self.assertEqual(qty_viols[0].severity, Severity.MUST_FIX)
        self.assertTrue(res.has_must_fix)

    def test_money_drift_classified_as_review(self):
        # 营业额 100 vs RHS 80+0+10+10+0+0 = 100. Inject 2 元 discount missing.
        # Set sales_price=102, RHS still 100 → delta=2, rel=2% → NEEDS_REVIEW
        res = check([clean_row(sales_price=102)], DEFAULT_IDENTITIES)
        money_viols = res.by_identity("金额恒等式")
        self.assertEqual(len(money_viols), 1)
        self.assertEqual(money_viols[0].severity, Severity.NEEDS_REVIEW)
        self.assertFalse(res.has_must_fix)

    def test_money_huge_drift_must_fix(self):
        # 营业额 500 vs RHS 100 → delta=400 (>100 AND >5%) → MUST_FIX
        res = check([clean_row(sales_price=500)], DEFAULT_IDENTITIES)
        self.assertTrue(res.has_must_fix)

    def test_missing_field_treated_as_must_fix(self):
        """Schema drift should surface, not silently pass."""
        bad = clean_row()
        del bad["free_amount"]
        res = check([bad], [AMOUNT_IDENTITY])
        self.assertEqual(len(res.violations), 1)
        self.assertEqual(res.violations[0].severity, Severity.MUST_FIX)

    def test_takeout_cancellation_bucket_passes(self):
        """Regression: takeout state=60 buckets had sales_price=0 + cancelled>0,
        but cancelled was on the RHS → 校验器 fired falsely. Fix removes cancelled
        from RHS; identity now holds because sales_price already excludes cancelled.
        """
        row = clean_row(
            qty=2, net_qty=0, free_qty=0, give_qty=0,
            refund_qty=0, cancelled_qty=2,
            sales_price=0,           # ttpos sales_price excludes state=60
            revenue=0,
            refund_amount=0,
            free_amount=0, give_amount=0, discount_amount=0,
            cancelled_amount=78,
            gross_amount=78,         # = sales_price(0) + cancelled_amount(78)
        )
        res = check([row], DEFAULT_IDENTITIES)
        self.assertEqual(res.violations, [],
                         "pure-cancellation bucket must not fire any identity")

    def test_mixed_active_and_cancelled_takeout_passes(self):
        """Same bucket has 20 active (¥39 each, all收) + 2 cancelled."""
        row = clean_row(
            qty=22, net_qty=20, free_qty=0, give_qty=0,
            refund_qty=0, cancelled_qty=2,
            sales_price=780,         # ttpos: 20 active × ¥39
            revenue=780,             # all active paid
            refund_amount=0, free_amount=0, give_amount=0, discount_amount=0,
            cancelled_amount=78,     # 2 × ¥39
            gross_amount=858,        # = sales_price(780) + cancelled_amount(78)
        )
        res = check([row], DEFAULT_IDENTITIES)
        self.assertEqual(res.violations, [],
                         "active+cancelled mix in takeout must not fire amount identity")

    def test_filter_by_severity(self):
        rows = [
            clean_row(),                                                   # clean
            clean_row(sales_price=101.5, gross_amount=101.5),              # money NEEDS_REVIEW (AMOUNT only)
            clean_row(net_qty=7),                                          # qty MUST_FIX (SALES_QTY only)
            clean_row(sales_price=600, gross_amount=600),                  # money MUST_FIX (AMOUNT only)
        ]
        res = check(rows, DEFAULT_IDENTITIES)
        self.assertEqual(len(res.by_severity(Severity.NEEDS_REVIEW)), 1)
        self.assertEqual(len(res.by_severity(Severity.MUST_FIX)), 2)


class IdentityIntegrityTests(unittest.TestCase):
    """The identities themselves should be 'real' — i.e. compose from
    fields that actually exist on `aggregate_with_bom` output."""

    REQUIRED_KEYS = {
        "qty", "net_qty", "free_qty", "give_qty", "refund_qty", "cancelled_qty",
        "sales_price", "revenue", "refund_amount",
        "free_amount", "give_amount", "cancelled_amount", "discount_amount",
    }

    def test_clean_row_has_all_required_keys(self):
        """If this fails, update either clean_row() or the identities — they
        diverged. This is a fence between the two layers."""
        row = clean_row()
        missing = self.REQUIRED_KEYS - set(row)
        self.assertEqual(missing, set(),
                         f"clean_row() missing keys used by identities: {missing}")


if __name__ == "__main__":
    unittest.main()
