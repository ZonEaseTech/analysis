"""复刻校验：erpnext_price 与 Go calculateFinalItemUnitCost / bom_with_erp_price_v4 对齐。

stdlib unittest（仓库约定，无需 pip install）。
"""
import os
import sys
import unittest

from tests import _setup  # noqa: F401  sys.path bootstrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bom_pipeline"))
from erpnext_price import (  # noqa: E402
    PricingRule,
    apply_pricing_rules,
    calculate_final_item_unit_cost,
    final_unit_cost,
    resolve_tax_rate,
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


if __name__ == "__main__":
    unittest.main()
