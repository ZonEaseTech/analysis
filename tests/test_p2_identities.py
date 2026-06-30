"""P2 — Source Coverage + Sanity Band identities 测试。

跑法: venv/bin/python -m unittest tests.test_p2_identities -v
"""
from __future__ import annotations

import unittest

from semantic.validators import check
from semantic.validators.core import Severity
from semantic.validators.identities import (
    BOM_SOURCE_COVERAGE,
    CANCEL_RATIO_BAND,
    DEFAULT_IDENTITIES,
    FREE_GIVE_RATIO_BAND,
    FULL_IDENTITIES,
    PRICE_SOURCE_COVERAGE,
    REFUND_RATIO_BAND,
    SANITY_BAND_IDENTITIES,
    SOURCE_COVERAGE_IDENTITIES,
    _band_classify,
    _coverage_classify,
)


# ═══════════════════════════════════════════════════════════════════
# _coverage_classify
# ═══════════════════════════════════════════════════════════════════

class CoverageClassifyTests(unittest.TestCase):
    def test_zero_is_negligible(self):
        self.assertEqual(_coverage_classify(0.0, 0.0), Severity.NEGLIGIBLE)

    def test_one_is_must_fix(self):
        self.assertEqual(_coverage_classify(1.0, 1.0), Severity.MUST_FIX)
        self.assertEqual(_coverage_classify(-1.0, -1.0), Severity.MUST_FIX)


# ═══════════════════════════════════════════════════════════════════
# _band_classify
# ═══════════════════════════════════════════════════════════════════

class BandClassifyTests(unittest.TestCase):
    def setUp(self):
        # 退款率: 0-5% OK; 5-20% review; >20% 必查
        self.classify = _band_classify(0.0, 0.05, hard_high=0.20)

    def test_in_band(self):
        self.assertEqual(self.classify(0.0, 0.03), Severity.NEGLIGIBLE)
        self.assertEqual(self.classify(0.0, 0.05), Severity.NEGLIGIBLE)
        self.assertEqual(self.classify(0.0, 0.00), Severity.NEGLIGIBLE)

    def test_soft_out_of_band_review(self):
        self.assertEqual(self.classify(0.0, 0.10), Severity.NEEDS_REVIEW)
        self.assertEqual(self.classify(0.0, 0.19), Severity.NEEDS_REVIEW)

    def test_hard_out_of_band_must_fix(self):
        self.assertEqual(self.classify(0.0, 0.25), Severity.MUST_FIX)
        self.assertEqual(self.classify(0.0, 0.99), Severity.MUST_FIX)

    def test_two_sided_band(self):
        # 食材率 25-40%; <15% / >50% 必查
        c = _band_classify(0.25, 0.40, hard_low=0.15, hard_high=0.50)
        self.assertEqual(c(0.0, 0.30), Severity.NEGLIGIBLE)
        self.assertEqual(c(0.0, 0.20), Severity.NEEDS_REVIEW)
        self.assertEqual(c(0.0, 0.45), Severity.NEEDS_REVIEW)
        self.assertEqual(c(0.0, 0.10), Severity.MUST_FIX)
        self.assertEqual(c(0.0, 0.55), Severity.MUST_FIX)


# ═══════════════════════════════════════════════════════════════════
# Source Coverage identities — opt-in (只在 row 显式带 source 字段时检查)
# ═══════════════════════════════════════════════════════════════════

class BomSourceCoverageTests(unittest.TestCase):
    def test_row_without_bom_source_skipped(self):
        """没 bom_source 字段的 row 不报警 (profit_by_price 这种报表)."""
        rows = [{"qty": 100, "item_name": "X"}]
        result = check(rows, [BOM_SOURCE_COVERAGE])
        self.assertEqual(result.violations, [])

    def test_explicit_no_bom_with_sales_is_violation(self):
        rows = [{"qty": 100, "bom_source": "无", "item_name": "鸡块"}]
        result = check(rows, [BOM_SOURCE_COVERAGE])
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].severity, Severity.MUST_FIX)

    def test_zero_qty_no_violation(self):
        """没销量的 SKU 即使没 BOM 来源也 OK."""
        rows = [{"qty": 0, "bom_source": "无", "item_name": "鸡块"}]
        result = check(rows, [BOM_SOURCE_COVERAGE])
        self.assertEqual(result.violations, [])

    def test_has_bom_source_passes(self):
        rows = [
            {"qty": 100, "bom_source": "market_20260513", "item_name": "X"},
            {"qty": 50, "bom_source": "bq_native", "item_name": "Y"},
        ]
        result = check(rows, [BOM_SOURCE_COVERAGE])
        self.assertEqual(result.violations, [])


class PriceSourceCoverageTests(unittest.TestCase):
    def test_row_without_price_source_skipped(self):
        rows = [{"qty": 100, "bom_source": "market"}]
        result = check(rows, [PRICE_SOURCE_COVERAGE])
        self.assertEqual(result.violations, [])

    def test_has_bom_but_no_price_source_violation(self):
        rows = [{
            "qty": 100, "bom_source": "market", "price_source": "无",
            "item_name": "X",
        }]
        result = check(rows, [PRICE_SOURCE_COVERAGE])
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].severity, Severity.MUST_FIX)

    def test_no_bom_no_price_source_ok(self):
        """没 BOM 时即使 price_source='无' 也 OK (没物料要算单价)."""
        rows = [{
            "qty": 100, "bom_source": "无", "price_source": "无",
            "item_name": "X",
        }]
        result = check(rows, [PRICE_SOURCE_COVERAGE])
        self.assertEqual(result.violations, [])

    def test_has_both_sources_passes(self):
        rows = [{
            "qty": 100, "bom_source": "market", "price_source": "ERPNext",
            "item_name": "X",
        }]
        result = check(rows, [PRICE_SOURCE_COVERAGE])
        self.assertEqual(result.violations, [])


# ═══════════════════════════════════════════════════════════════════
# Sanity Band identities
# ═══════════════════════════════════════════════════════════════════

class RefundRatioBandTests(unittest.TestCase):
    def _make_row(self, qty, refund_qty):
        return {
            "qty": qty, "refund_qty": refund_qty, "item_name": "X",
            # 占位字段防 KeyError
            "free_qty": 0, "give_qty": 0, "cancelled_qty": 0, "net_qty": qty,
            "sales_price": 0, "revenue": 0, "refund_amount": 0,
            "free_amount": 0, "give_amount": 0, "discount_amount": 0,
        }

    def test_in_band(self):
        rows = [self._make_row(100, 3)]   # 3%
        result = check(rows, [REFUND_RATIO_BAND])
        self.assertEqual(result.violations, [])

    def test_soft_review(self):
        rows = [self._make_row(100, 10)]  # 10%
        result = check(rows, [REFUND_RATIO_BAND])
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].severity, Severity.NEEDS_REVIEW)

    def test_hard_must_fix(self):
        rows = [self._make_row(100, 25)]  # 25%
        result = check(rows, [REFUND_RATIO_BAND])
        self.assertEqual(result.violations[0].severity, Severity.MUST_FIX)

    def test_zero_qty_safe(self):
        rows = [self._make_row(0, 0)]
        result = check(rows, [REFUND_RATIO_BAND])
        self.assertEqual(result.violations, [])


class FreeGiveRatioBandTests(unittest.TestCase):
    def test_in_band(self):
        rows = [{"qty": 100, "free_qty": 3, "give_qty": 2}]  # 5%
        result = check(rows, [FREE_GIVE_RATIO_BAND])
        self.assertEqual(result.violations, [])

    def test_soft_review(self):
        rows = [{"qty": 100, "free_qty": 10, "give_qty": 10}]  # 20%
        result = check(rows, [FREE_GIVE_RATIO_BAND])
        self.assertEqual(result.violations[0].severity, Severity.NEEDS_REVIEW)

    def test_hard_must_fix(self):
        rows = [{"qty": 100, "free_qty": 30, "give_qty": 10}]  # 40%
        result = check(rows, [FREE_GIVE_RATIO_BAND])
        self.assertEqual(result.violations[0].severity, Severity.MUST_FIX)


class CancelRatioBandTests(unittest.TestCase):
    def test_in_band(self):
        rows = [{"qty": 100, "cancelled_qty": 5}]  # 5%
        result = check(rows, [CANCEL_RATIO_BAND])
        self.assertEqual(result.violations, [])

    def test_soft_review(self):
        rows = [{"qty": 100, "cancelled_qty": 20}]  # 20%
        result = check(rows, [CANCEL_RATIO_BAND])
        self.assertEqual(result.violations[0].severity, Severity.NEEDS_REVIEW)

    def test_hard_must_fix(self):
        rows = [{"qty": 100, "cancelled_qty": 40}]  # 40%
        result = check(rows, [CANCEL_RATIO_BAND])
        self.assertEqual(result.violations[0].severity, Severity.MUST_FIX)


# ═══════════════════════════════════════════════════════════════════
# Bundles 完整性
# ═══════════════════════════════════════════════════════════════════

class BundlesTests(unittest.TestCase):
    def test_default_unchanged(self):
        """DEFAULT 是 3 个 (销量 + 金额 + 毛额守恒)."""
        self.assertEqual(len(DEFAULT_IDENTITIES), 3)

    def test_source_coverage_bundle(self):
        self.assertEqual(len(SOURCE_COVERAGE_IDENTITIES), 2)

    def test_sanity_band_bundle(self):
        self.assertEqual(len(SANITY_BAND_IDENTITIES), 3)

    def test_full_bundle_is_union(self):
        self.assertEqual(
            len(FULL_IDENTITIES),
            len(DEFAULT_IDENTITIES)
            + len(SOURCE_COVERAGE_IDENTITIES)
            + len(SANITY_BAND_IDENTITIES),
        )

    def test_full_safe_on_minimal_row(self):
        """没 source / no rates 的最小 row 跑 FULL 不应该 crash 或大量误报."""
        rows = [{
            "qty": 10, "net_qty": 10,
            "free_qty": 0, "give_qty": 0, "refund_qty": 0, "cancelled_qty": 0,
            "sales_price": 100, "revenue": 100,
            "refund_amount": 0, "free_amount": 0, "give_amount": 0,
            "discount_amount": 0, "cancelled_amount": 0,
            "gross_amount": 100,  # = sales_price(100) + cancelled_amount(0)
        }]
        result = check(rows, FULL_IDENTITIES)
        # 没 source 字段 → coverage opt-in 跳过; 没异常率 → bands pass
        # 金额恒等式: sales(100) = actual(100)+0+0+0+0 ✓
        # 毛额恒等式: gross(100) = sales(100)+cancelled(0) ✓
        self.assertEqual(result.violations, [])


if __name__ == "__main__":
    unittest.main()
