"""Fallback BOM fuzzy match — protect the 5-level priority described in the docstring.

This is what the branch `fix-fallback-bom-fuzzy-match` exists to enforce.
The headline regression: '鸡肉芝士球' must NOT match the promo-bundle row
'周一特惠 - 鸡肉芝士球 2 盒 69', because the bundle BOM is for 2 boxes.
"""
import unittest

from tests import _setup  # noqa: F401

from bq_reports.profit_margin_report import (
    BOM_DROP_CODES,
    BOM_REPLACEMENTS,
    _apply_bom_overrides,
    _match_fallback_bom,
)


def bom(*tuples):
    """Inline shorthand for fallback-bom list-of-tuples values."""
    return list(tuples)


class MatchFallbackBomLevels(unittest.TestCase):
    """Walk down the 5-level priority cascade from strictest to loosest."""

    def test_level1_exact_full_key(self):
        boms = {"鸡块": bom(("M1", "鸡块原料", 100.0, "g"))}
        self.assertEqual(_match_fallback_bom("鸡块", boms), boms["鸡块"])

    def test_level2_zh_segment_exact(self):
        boms = {"鸡块 / Chicken Nugget": bom(("M1", "原料", 50.0, "g"))}
        self.assertEqual(
            _match_fallback_bom("鸡块", boms),
            boms["鸡块 / Chicken Nugget"],
        )

    def test_level3_zh_startswith_picks_shortest(self):
        """item='鸡块' matches both '鸡块（中）' and '鸡块（特大）' — take the shorter."""
        boms = {
            "鸡块（特大）/ XL Nugget": bom(("M2", "特大原料", 200.0, "g")),
            "鸡块（中）/ M Nugget":   bom(("M1", "中原料",   100.0, "g")),
        }
        matched = _match_fallback_bom("鸡块", boms)
        self.assertEqual(matched, boms["鸡块（中）/ M Nugget"])

    def test_level4_zh_contains_picks_shortest(self):
        boms = {
            "鸡肉芝士球（单点）": bom(("M3", "单点原料", 80.0, "g")),
            "周一特惠 - 鸡肉芝士球 2 盒 69": bom(("M4", "2盒装原料", 160.0, "g")),
        }
        matched = _match_fallback_bom("鸡肉芝士球", boms)
        # Shorter zh segment wins — the single-serving one, NOT the 2-box bundle.
        self.assertEqual(
            matched, boms["鸡肉芝士球（单点）"],
            "Regression: bundle BOM must not steal the single-item match.",
        )

    def test_level5_long_prefix_loose(self):
        """≥5-char items: first 10 chars appearing inside any zh segment qualifies."""
        boms = {"超长前缀A B C D E F G": bom(("M5", "x", 1.0, "g"))}
        matched = _match_fallback_bom("超长前缀A B C", boms)
        # Prefix '超长前缀A B C' (10 chars incl spaces) is inside the key → match.
        self.assertEqual(matched, boms["超长前缀A B C D E F G"])

    def test_no_match_returns_none(self):
        self.assertIsNone(_match_fallback_bom("不存在的商品", {"鸡块": bom()}))

    def test_empty_inputs(self):
        self.assertIsNone(_match_fallback_bom("", {"X": bom()}))
        self.assertIsNone(_match_fallback_bom("X", {}))
        self.assertIsNone(_match_fallback_bom(None, {"X": bom()}))
        self.assertIsNone(_match_fallback_bom("   ", {"X": bom()}))

    def test_short_item_skips_level5(self):
        """<5-char item must not fall back to loose-prefix matching."""
        boms = {"无关的长名字": bom(("M", "x", 1.0, "g"))}
        # '鸡块' is len=2 — level 5 disabled, level 4 also fails.
        self.assertIsNone(_match_fallback_bom("鸡块", boms))


class BomOverrideRules(unittest.TestCase):
    """`_apply_bom_overrides` enforces BOM_REPLACEMENTS + BOM_DROP_CODES.

    Locked rules (committed business config, not arbitrary):
      - FR01008 → FR02001 (preserve original name)
      - VE01001 → MK01018 (preserve original name)
      - TL99008 dropped entirely
    Replacement also merges quantity into existing target if both present.
    """

    def _rec(self, code, num, name="x", unit="g", conv=1, price=0):
        return (code, name, num, unit, conv, price)

    def test_drop_code_removed(self):
        self.assertIn("TL99008", BOM_DROP_CODES)
        out = _apply_bom_overrides([self._rec("TL99008", 5), self._rec("KEEP", 1)])
        codes = [r[0] for r in out]
        self.assertNotIn("TL99008", codes)
        self.assertIn("KEEP", codes)

    def test_replacement_rewrites_code(self):
        self.assertEqual(BOM_REPLACEMENTS["FR01008"][0], "FR02001")
        out = _apply_bom_overrides([self._rec("FR01008", 10)])
        self.assertEqual(out[0][0], "FR02001")

    def test_replacement_merges_into_existing_target(self):
        """If target code already present, qty is summed; metadata from real material wins."""
        out = _apply_bom_overrides([
            self._rec("FR02001", 3, name="真实物料"),
            self._rec("FR01008", 7, name="旧名字"),       # replaced → merged into FR02001
        ])
        self.assertEqual(len(out), 1)
        code, name, num, _u, _c, _p = out[0]
        self.assertEqual(code, "FR02001")
        self.assertEqual(num, 10)
        self.assertEqual(name, "真实物料",
                         "Original target's name must not be overwritten by replacement source.")


if __name__ == "__main__":
    unittest.main()
