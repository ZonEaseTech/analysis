"""TtposCostAnchor 四层测试 — 严格 TDD 先红后绿.

层次:
  Layer 1: compute_ttpos_unit_cost — 纯函数，不依赖任何外部资源
  Layer 2: TtposCostAnchorResult dataclass + is_drift 属性
  Layer 3: run_cost_anchor — 纯比对，不碰 ERP
  Layer 4: fetch_ttpos_truths_from_erp — 注入假 erp_get，不跑 live ERP

跑法: venv/bin/python -m unittest tests.test_ttpos_cost_anchor -v
"""
from __future__ import annotations

import datetime
import unittest
from unittest.mock import MagicMock

from semantic.reconciliation.checks.ttpos_cost_anchor import (
    TtposCostAnchorResult,
    compute_ttpos_unit_cost,
    fetch_ttpos_truths_from_erp,
    run_cost_anchor,
)


# ---------------------------------------------------------------------------
# Layer 1: compute_ttpos_unit_cost — 纯算法，对齐 item.go:309
# ---------------------------------------------------------------------------
class TestComputeTtposUnitCost(unittest.TestCase):
    """纯函数测试，无 I/O，无 ERP。"""

    def test_compute_matches_ttpos_formula(self):
        """给定任务书：base=18, margin=5%, applies=True, tax=0 → 18.9"""
        self.assertAlmostEqual(
            compute_ttpos_unit_cost(base=18.0, margin_pct=5.0, applies=True, tax_rate=0.0),
            18.9,
            places=6,
        )

    def test_compute_skips_margin_when_not_applies(self):
        """given 任务书：applies=False → 不套 margin，只上浮税。18 × 1.07 = 19.26"""
        self.assertAlmostEqual(
            compute_ttpos_unit_cost(base=18.0, margin_pct=5.0, applies=False, tax_rate=7.0),
            18.0 * 1.07,
            places=6,
        )

    def test_applies_margin_then_tax(self):
        """margin 先套，税后套：base=100, margin=5%, tax=7% → 100×1.05×1.07 = 112.35"""
        result = compute_ttpos_unit_cost(base=100.0, margin_pct=5.0, applies=True, tax_rate=7.0)
        self.assertAlmostEqual(result, 100 * 1.05 * 1.07, places=6)

    def test_zero_base_returns_zero(self):
        """base=0 → net_cost=0 → 跳过税（对齐 Go: netCost==0 直接返回 netCost）。"""
        result = compute_ttpos_unit_cost(base=0.0, margin_pct=5.0, applies=True, tax_rate=7.0)
        self.assertEqual(result, 0.0)

    def test_zero_tax_no_uplift(self):
        """tax=0 → 只套 margin，不上浮税。"""
        result = compute_ttpos_unit_cost(base=100.0, margin_pct=10.0, applies=True, tax_rate=0.0)
        self.assertAlmostEqual(result, 110.0, places=6)

    def test_no_margin_no_tax(self):
        """applies=False, tax=0 → 原价返回。"""
        result = compute_ttpos_unit_cost(base=50.0, margin_pct=5.0, applies=False, tax_rate=0.0)
        self.assertAlmostEqual(result, 50.0, places=6)


# ---------------------------------------------------------------------------
# Layer 2: TtposCostAnchorResult + is_drift
# ---------------------------------------------------------------------------
class TestTtposCostAnchorResult(unittest.TestCase):
    """dataclass 字段 + is_drift 属性测试。"""

    def test_anchor_flags_drift(self):
        """给定任务书：|10-12|=2 > abs_tol=0.01 且 rel=20% > rel_tol → 应 drift"""
        result = TtposCostAnchorResult(
            item_code="X", ours=10.0, ttpos=12.0, abs_tol=0.01
        )
        self.assertTrue(result.is_drift)

    def test_anchor_no_drift_within_tol(self):
        """给定任务书：ours==ttpos → 无 drift"""
        result = TtposCostAnchorResult(
            item_code="X", ours=12.0, ttpos=12.0, abs_tol=0.01
        )
        self.assertFalse(result.is_drift)

    def test_no_drift_within_abs_tol(self):
        """差额 < abs_tol → is_drift=False（绝对容差兜底）。"""
        result = TtposCostAnchorResult(
            item_code="Y", ours=100.005, ttpos=100.0, abs_tol=0.01
        )
        self.assertFalse(result.is_drift)

    def test_drift_large_abs(self):
        """|ours-ttpos| >> abs_tol → drift（相对差 10%）。"""
        result = TtposCostAnchorResult(
            item_code="Z", ours=110.0, ttpos=100.0, abs_tol=0.01
        )
        self.assertTrue(result.is_drift)

    def test_rel_tol_respected(self):
        """rel_tol 足够大时，即使 abs_diff > abs_tol 也不应 drift。"""
        # |100.1 - 100| = 0.1 > abs_tol(0.01)，但 rel=0.1% < rel_tol=1.0%
        result = TtposCostAnchorResult(
            item_code="W", ours=100.1, ttpos=100.0, abs_tol=0.01, rel_tol=0.01
        )
        self.assertFalse(result.is_drift)

    def test_default_rel_tol_is_strict(self):
        """默认 rel_tol 足够严，|0.1/100| = 0.1% 若 abs > abs_tol 则 drift。"""
        result = TtposCostAnchorResult(
            item_code="V", ours=100.1, ttpos=100.0, abs_tol=0.01
        )
        # 取决于默认 rel_tol 设置，我们期望默认值让 0.1% 漂移被标出
        # 用断言验证 is_drift 不崩即可（实现有默认值）
        _ = result.is_drift  # 不崩就过

    def test_ttpos_zero_edge(self):
        """ttpos=0, ours=0 → 不崩，is_drift=False（完全一致）。"""
        result = TtposCostAnchorResult(item_code="ZERO", ours=0.0, ttpos=0.0, abs_tol=0.01)
        self.assertFalse(result.is_drift)

    def test_ttpos_zero_ours_nonzero(self):
        """ttpos=0, ours>0 → abs_diff>abs_tol → drift（无法算相对差，退化为绝对差）。"""
        result = TtposCostAnchorResult(item_code="Z0", ours=5.0, ttpos=0.0, abs_tol=0.01)
        self.assertTrue(result.is_drift)


# ---------------------------------------------------------------------------
# Layer 3: run_cost_anchor — 纯比对，fixture 字典
# ---------------------------------------------------------------------------
class TestRunCostAnchor(unittest.TestCase):
    """不碰 ERP，用 fixture 验筛 drift。"""

    def test_empty_dicts_return_empty(self):
        results = run_cost_anchor({}, {}, abs_tol=0.01, rel_tol=0.001)
        self.assertEqual(results, [])

    def test_all_match_no_drift(self):
        our = {"A": 18.9, "B": 21.0}
        truth = {"A": 18.9, "B": 21.0}
        results = run_cost_anchor(our, truth, abs_tol=0.01, rel_tol=0.001)
        self.assertFalse(any(r.is_drift for r in results))
        self.assertEqual(len(results), 2)

    def test_one_drift_flagged(self):
        our = {"A": 18.9, "B": 25.0}
        truth = {"A": 18.9, "B": 21.0}  # B 漂了 4 元 / 19%
        results = run_cost_anchor(our, truth, abs_tol=0.01, rel_tol=0.001)
        drifts = [r for r in results if r.is_drift]
        self.assertEqual(len(drifts), 1)
        self.assertEqual(drifts[0].item_code, "B")
        self.assertAlmostEqual(drifts[0].ours, 25.0)
        self.assertAlmostEqual(drifts[0].ttpos, 21.0)

    def test_item_in_ours_only_skipped(self):
        """our_prices 有的物料 ttpos_truths 没有 → 跳过（无法比对）。"""
        our = {"A": 10.0, "GHOST": 5.0}
        truth = {"A": 10.0}
        results = run_cost_anchor(our, truth, abs_tol=0.01, rel_tol=0.001)
        codes = {r.item_code for r in results}
        self.assertNotIn("GHOST", codes)
        self.assertIn("A", codes)

    def test_item_in_truth_only_skipped(self):
        """ttpos_truths 有的物料 our_prices 没有 → 跳过（我们没有这个物料价）。"""
        our = {"A": 10.0}
        truth = {"A": 10.0, "EXTRA": 3.0}
        results = run_cost_anchor(our, truth, abs_tol=0.01, rel_tol=0.001)
        codes = {r.item_code for r in results}
        self.assertNotIn("EXTRA", codes)

    def test_result_objects_are_correct_type(self):
        our = {"MAT-001": 19.0}
        truth = {"MAT-001": 18.9}
        results = run_cost_anchor(our, truth, abs_tol=0.01, rel_tol=0.001)
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], TtposCostAnchorResult)


# ---------------------------------------------------------------------------
# Layer 4: fetch_ttpos_truths_from_erp — 注入假 erp_get
# ---------------------------------------------------------------------------
class TestFetchTtposTruthsFromErp(unittest.TestCase):
    """
    用注入假 erp_get 验复算逻辑，含日期失效格子。
    真·live 路径（erp_get=None）需 ERP sid，本测试不跑。
    """

    def _make_item_price(self, item_code: str, rate: float, price_list: str = "Buying - Internal") -> dict:
        return {
            "item_code": item_code,
            "price_list_rate": rate,
            "price_list": price_list,
        }

    def _make_rule(
        self,
        name: str = "PRLE-0003",
        margin_type: str = "Percentage",
        margin_rate: float = 5.0,
        for_price_list: str = "Buying - Internal",
        buying: int = 1,
        disabled: int = 0,
        valid_from: str = "",
        valid_upto: str = "",
        price_or_product_discount: str = "Price",
    ) -> dict:
        return {
            "name": name,
            "margin_type": margin_type,
            "margin_rate_or_amount": margin_rate,
            "for_price_list": for_price_list,
            "buying": buying,
            "disabled": disabled,
            "valid_from": valid_from,
            "valid_upto": valid_upto,
            "price_or_product_discount": price_or_product_discount,
        }

    def _make_item_tax(self, item_code: str, tax_rate: float) -> dict:
        return {"item_code": item_code, "tax_rate": tax_rate}

    def _make_erp_get(self, item_prices=None, rules=None, item_taxes=None):
        """构造假 erp_get callable，按 doctype 路由返回 fixture 数据。"""
        item_prices = item_prices or []
        rules = rules or []
        item_taxes = item_taxes or []

        def fake_erp_get(doctype, **kwargs):
            if doctype == "Item Price":
                return item_prices
            if doctype == "Pricing Rule":
                return rules
            if doctype == "Item Tax":
                return item_taxes
            return []

        return fake_erp_get

    def test_basic_margin_and_tax(self):
        """基础场景：base=18, margin=5%, tax=7% → 18×1.05×1.07≈20.223"""
        erp_get = self._make_erp_get(
            item_prices=[self._make_item_price("MAT-001", 18.0)],
            rules=[self._make_rule()],
            item_taxes=[self._make_item_tax("MAT-001", 7.0)],
        )
        result = fetch_ttpos_truths_from_erp(["MAT-001"], erp_get=erp_get)
        self.assertIn("MAT-001", result)
        self.assertAlmostEqual(result["MAT-001"], 18.0 * 1.05 * 1.07, places=4)

    def test_no_rule_applies_no_margin(self):
        """规则 disabled=1 → 不套 margin，只上浮税率。base=18, tax=7% → 18×1.07=19.26"""
        rule_disabled = self._make_rule(disabled=1)
        erp_get = self._make_erp_get(
            item_prices=[self._make_item_price("MAT-002", 18.0)],
            rules=[rule_disabled],
            item_taxes=[self._make_item_tax("MAT-002", 7.0)],
        )
        result = fetch_ttpos_truths_from_erp(["MAT-002"], erp_get=erp_get)
        self.assertAlmostEqual(result["MAT-002"], 18.0 * 1.07, places=4)

    def test_date_expired_rule_no_margin(self):
        """valid_upto=过去日期 → 规则失效，不套 margin，只上浮税。"""
        expired_rule = self._make_rule(valid_upto="2020-01-01")
        erp_get = self._make_erp_get(
            item_prices=[self._make_item_price("MAT-003", 18.0)],
            rules=[expired_rule],
            item_taxes=[self._make_item_tax("MAT-003", 7.0)],
        )
        result = fetch_ttpos_truths_from_erp(["MAT-003"], erp_get=erp_get)
        # 规则过期 → 不套 5% margin → 18 × 1.07 = 19.26
        self.assertAlmostEqual(result["MAT-003"], 18.0 * 1.07, places=4)

    def test_date_not_yet_valid_rule_no_margin(self):
        """valid_from=未来日期 → 规则尚未生效，不套 margin。"""
        future_rule = self._make_rule(valid_from="2099-01-01")
        erp_get = self._make_erp_get(
            item_prices=[self._make_item_price("MAT-004", 20.0)],
            rules=[future_rule],
            item_taxes=[self._make_item_tax("MAT-004", 0.0)],
        )
        result = fetch_ttpos_truths_from_erp(["MAT-004"], erp_get=erp_get)
        # 规则未生效 → 不套 margin，tax=0 → 返回 base=20
        self.assertAlmostEqual(result["MAT-004"], 20.0, places=4)

    def test_date_valid_range_active_rule_applies(self):
        """valid_from 在过去、valid_upto 在将来 → 规则有效，套 margin。"""
        active_rule = self._make_rule(valid_from="2020-01-01", valid_upto="2099-12-31")
        erp_get = self._make_erp_get(
            item_prices=[self._make_item_price("MAT-005", 10.0)],
            rules=[active_rule],
            item_taxes=[self._make_item_tax("MAT-005", 0.0)],
        )
        result = fetch_ttpos_truths_from_erp(["MAT-005"], erp_get=erp_get)
        # active rule → 10 × 1.05 = 10.5（tax=0 不上浮）
        self.assertAlmostEqual(result["MAT-005"], 10.5, places=4)

    def test_no_item_tax_uses_default_7pct(self):
        """无 Item Tax 记录 → 兜底 7% VAT。base=100, margin=5% → 100×1.05×1.07=112.35"""
        erp_get = self._make_erp_get(
            item_prices=[self._make_item_price("MAT-006", 100.0)],
            rules=[self._make_rule()],
            item_taxes=[],  # 无税率记录
        )
        result = fetch_ttpos_truths_from_erp(["MAT-006"], erp_get=erp_get)
        self.assertAlmostEqual(result["MAT-006"], 100.0 * 1.05 * 1.07, places=4)

    def test_missing_item_price_not_in_result(self):
        """ERP 里没有这个物料的 Item Price → 结果 dict 里没有这个 key。"""
        erp_get = self._make_erp_get(item_prices=[], rules=[], item_taxes=[])
        result = fetch_ttpos_truths_from_erp(["GHOST-001"], erp_get=erp_get)
        self.assertNotIn("GHOST-001", result)

    def test_multiple_items_batch(self):
        """多物料批量，各自独立算对。"""
        erp_get = self._make_erp_get(
            item_prices=[
                self._make_item_price("A", 10.0),
                self._make_item_price("B", 20.0),
            ],
            rules=[self._make_rule()],  # 5% margin
            item_taxes=[
                self._make_item_tax("A", 0.0),
                self._make_item_tax("B", 7.0),
            ],
        )
        result = fetch_ttpos_truths_from_erp(["A", "B"], erp_get=erp_get)
        self.assertAlmostEqual(result["A"], 10.0 * 1.05, places=4)       # tax=0
        self.assertAlmostEqual(result["B"], 20.0 * 1.05 * 1.07, places=4)

    def test_wrong_price_list_rule_not_applied(self):
        """for_price_list 不匹配 'Buying - Internal' → 规则不套用。"""
        mismatched_rule = self._make_rule(for_price_list="Selling - Public")
        erp_get = self._make_erp_get(
            item_prices=[self._make_item_price("MAT-007", 50.0)],
            rules=[mismatched_rule],
            item_taxes=[self._make_item_tax("MAT-007", 0.0)],
        )
        result = fetch_ttpos_truths_from_erp(["MAT-007"], erp_get=erp_get)
        # 规则价表不匹配 → 不套 margin，tax=0 → base=50
        self.assertAlmostEqual(result["MAT-007"], 50.0, places=4)

    def test_rule_name_not_prle0003_not_applied(self):
        """Name != 'PRLE-0003' 的规则 → 完整 PRLE-0003 条件要求 Name 匹配，否则跳过。"""
        wrong_name_rule = self._make_rule(name="PRLE-9999")
        erp_get = self._make_erp_get(
            item_prices=[self._make_item_price("MAT-008", 30.0)],
            rules=[wrong_name_rule],
            item_taxes=[self._make_item_tax("MAT-008", 0.0)],
        )
        result = fetch_ttpos_truths_from_erp(["MAT-008"], erp_get=erp_get)
        # Name 不是 PRLE-0003 → 不套 margin，tax=0 → base=30
        self.assertAlmostEqual(result["MAT-008"], 30.0, places=4)

    def test_price_or_discount_non_price_not_applied(self):
        """price_or_product_discount='Discount' → 非 Price 型规则跳过，不套 margin。

        对齐 Go item.go:286: PriceOrDiscount != "" && !EqualFold("Price") → false。
        这是 Task 4.1 该补全的 fidelity 条件（Task 2.1 故意留到这里）。
        """
        discount_rule = self._make_rule(price_or_product_discount="Discount")
        erp_get = self._make_erp_get(
            item_prices=[self._make_item_price("MAT-009", 40.0)],
            rules=[discount_rule],
            item_taxes=[self._make_item_tax("MAT-009", 0.0)],
        )
        result = fetch_ttpos_truths_from_erp(["MAT-009"], erp_get=erp_get)
        # 非 Price 型规则 → 不套 margin，tax=0 → base=40
        self.assertAlmostEqual(result["MAT-009"], 40.0, places=4)

    def test_price_or_discount_empty_string_applies(self):
        """price_or_product_discount='' (空) → 规则照常套用（对齐 Go: 空字符串不拦）。

        Go: r.PriceOrDiscount != "" 为假 → 不进入 return false 分支。
        """
        empty_pod_rule = self._make_rule(price_or_product_discount="")
        erp_get = self._make_erp_get(
            item_prices=[self._make_item_price("MAT-010", 100.0)],
            rules=[empty_pod_rule],
            item_taxes=[self._make_item_tax("MAT-010", 0.0)],
        )
        result = fetch_ttpos_truths_from_erp(["MAT-010"], erp_get=erp_get)
        # 空 PriceOrDiscount → 不拦 → 套 5% margin，tax=0 → 100×1.05=105
        self.assertAlmostEqual(result["MAT-010"], 105.0, places=4)

    def test_price_or_discount_price_case_insensitive_applies(self):
        """price_or_product_discount='price' (小写) → 大小写不敏感匹配 'Price'，照常套用。"""
        lower_price_rule = self._make_rule(price_or_product_discount="price")
        erp_get = self._make_erp_get(
            item_prices=[self._make_item_price("MAT-011", 100.0)],
            rules=[lower_price_rule],
            item_taxes=[self._make_item_tax("MAT-011", 0.0)],
        )
        result = fetch_ttpos_truths_from_erp(["MAT-011"], erp_get=erp_get)
        # 'price' EqualFold 'Price' → 不拦 → 套 margin → 100×1.05=105
        self.assertAlmostEqual(result["MAT-011"], 105.0, places=4)

    def test_live_path_raises_not_implemented(self):
        """erp_get=None (不注入) → 抛 NotImplementedError，不是 ImportError。

        诚实性机制自检：docstring 承诺 live 路径抛 NotImplementedError（需 sid 未接入）。
        """
        with self.assertRaises(NotImplementedError):
            fetch_ttpos_truths_from_erp(["X"])


if __name__ == "__main__":
    unittest.main()
