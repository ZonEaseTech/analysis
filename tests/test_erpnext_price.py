"""复刻校验：erpnext_price 与 Go calculateFinalItemUnitCost / bom_with_erp_price_v4 对齐。

stdlib unittest（仓库约定，无需 pip install）。
"""
import os
import sys
import unittest

from tests import _setup  # noqa: F401  sys.path bootstrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bom_pipeline"))
from erpnext_price import (  # noqa: E402
    DEFAULT_BUYING_RULE,
    PricingRule,
    apply_pricing_rules,
    calculate_final_item_unit_cost,
    final_unit_cost,
    final_unit_cost_with_rule,
    resolve_tax_rate,
    rule_applies,
)

# 基准用版本库内的规范源 clean_bom.csv（含 基价(原始)/适用税率%/ERPNext新单价 同列，
# 与 bom_with_erp_price_v4.xlsx 0 差异；v4 原件已归档 exports/_archive/）
BASELINE = os.path.join(
    os.path.dirname(__file__), "..", "resources", "wallace.20260626", "clean_bom.csv")


class TestPricingRules(unittest.TestCase):
    def test_percentage_rule(self):
        self.assertAlmostEqual(apply_pricing_rules(100, [PricingRule("Percentage", 5)]), 105)

    def test_amount_rule(self):
        self.assertAlmostEqual(apply_pricing_rules(100, [PricingRule("Amount", 3)]), 103)

    def test_unknown_rule_skipped(self):
        self.assertEqual(apply_pricing_rules(100, [PricingRule("Weird", 5)]), 100)

    def test_case_insensitive(self):
        self.assertAlmostEqual(apply_pricing_rules(100, [PricingRule("percentage", 5)]), 105)


class TestFinalCost(unittest.TestCase):
    def test_zero_net_skips_tax(self):
        self.assertEqual(calculate_final_item_unit_cost(0, [PricingRule("Percentage", 5)], 7), 0)

    def test_zero_tax_skips_tax(self):
        self.assertAlmostEqual(
            calculate_final_item_unit_cost(18, [PricingRule("Percentage", 5)], 0), 18.9)

    def test_default_tax_fallback(self):
        self.assertEqual(resolve_tax_rate(None), 7)
        self.assertEqual(resolve_tax_rate(0), 0)

    def test_final_unit_cost_default_margin_and_tax(self):
        self.assertAlmostEqual(final_unit_cost(10), 10 * 1.05 * 1.07)


class TestV4Baseline(unittest.TestCase):
    def test_matches_baseline(self):
        if not os.path.exists(BASELINE):
            self.skipTest("缺 clean_bom.csv 基准")
        import csv
        with open(BASELINE, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        ok = bad = 0
        for r in rows:
            base, tax, exp = r["基价(原始)"], r["适用税率%"], r["ERPNext新单价"]
            if not base or not exp:
                continue
            got = final_unit_cost(float(base), None if tax in (None, "") else float(tax))
            if abs(got - float(exp)) < 1e-9:
                ok += 1
            else:
                bad += 1
        self.assertEqual(bad, 0)
        self.assertGreater(ok, 4000)  # 基准约 4465 行


class TestRuleApplies(unittest.TestCase):
    """final_unit_cost_with_rule / rule_applies 的条件判定（appliesToItemUnitCost 子集）。"""

    def test_rule_skipped_when_price_list_mismatch(self):
        rule = PricingRule(margin_type="Percentage", margin_rate_or_amount=5.0,
                           for_price_list="Buying - Internal", buying=True, disabled=False)
        # 当前价表 = Standard Buying → 规则不适用 → 仅税
        self.assertAlmostEqual(
            final_unit_cost_with_rule(100.0, tax_rate=7, rule=rule,
                                      price_list="Standard Buying"),
            107.0)
        # 当前价表 = Buying - Internal → 规则适用 → ×1.05 再 ×1.07
        self.assertAlmostEqual(
            final_unit_cost_with_rule(100.0, tax_rate=7, rule=rule,
                                      price_list="Buying - Internal"),
            100 * 1.05 * 1.07)

    def test_rule_skipped_when_disabled(self):
        rule = PricingRule("Percentage", 5.0, for_price_list="", buying=True, disabled=True)
        self.assertAlmostEqual(
            final_unit_cost_with_rule(100.0, 7, rule, "Buying - Internal"), 107.0)

    def test_rule_applies_empty_for_price_list_matches_any(self):
        """for_price_list 为空字符串时，任意价表都适用。"""
        rule = PricingRule("Percentage", 5.0, for_price_list="", buying=True, disabled=False)
        self.assertTrue(rule_applies(rule, "Standard Buying"))
        self.assertTrue(rule_applies(rule, "Buying - Internal"))
        self.assertAlmostEqual(
            final_unit_cost_with_rule(100.0, 7, rule, "Standard Buying"),
            100 * 1.05 * 1.07)

    def test_rule_price_list_case_insensitive(self):
        """价表名匹配大小写不敏感。"""
        rule = PricingRule("Percentage", 5.0, for_price_list="buying - internal",
                           buying=True, disabled=False)
        self.assertTrue(rule_applies(rule, "Buying - Internal"))
        self.assertTrue(rule_applies(rule, "BUYING - INTERNAL"))

    def test_old_final_unit_cost_stays_unconditional(self):
        """旧入口 final_unit_cost 保持无条件结果（base×1.05×(1+tax)），与 v4 口径一致。"""
        self.assertAlmostEqual(final_unit_cost(100.0), 100 * 1.05 * 1.07)
        self.assertAlmostEqual(final_unit_cost(100.0, 0), 100 * 1.05)

    def test_pricing_rule_backward_compatible_positional(self):
        """PricingRule(*DEFAULT_BUYING_RULE) 仍能构造（新字段有默认值）。"""
        r = PricingRule(*DEFAULT_BUYING_RULE)
        self.assertEqual(r.margin_type, "Percentage")
        self.assertAlmostEqual(r.margin_rate_or_amount, 5.0)
        self.assertEqual(r.for_price_list, "")
        self.assertTrue(r.buying)
        self.assertFalse(r.disabled)


if __name__ == "__main__":
    unittest.main()
