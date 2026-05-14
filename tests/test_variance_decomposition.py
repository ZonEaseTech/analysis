"""P5 — variance_decomposition 测试。

跑法: venv/bin/python -m unittest tests.test_variance_decomposition -v
"""
from __future__ import annotations

import unittest

from semantic.analytics import (
    GrossProfitVariance,
    Variance,
    decompose_gross_profit,
    decompose_revenue,
)


# ═══════════════════════════════════════════════════════════════════
# Revenue 2 维分解
# ═══════════════════════════════════════════════════════════════════

class RevenueDecomposeTests(unittest.TestCase):
    def test_only_qty_change(self):
        """单纯销量变化, 价不变."""
        vs = decompose_revenue(
            previous_qty=100, previous_price=10,
            current_qty=120, current_price=10,
        )
        # 量差 = 20 × 10 = 200; 价差 = 0
        self.assertEqual(vs[0].name, "量差")
        self.assertEqual(vs[0].amount, 200)
        self.assertEqual(vs[1].name, "价差")
        self.assertEqual(vs[1].amount, 0)

    def test_only_price_change(self):
        """单纯价格变化, 量不变."""
        vs = decompose_revenue(
            previous_qty=100, previous_price=10,
            current_qty=100, current_price=12,
        )
        # 量差 = 0; 价差 = 2 × 100 = 200
        self.assertEqual(vs[0].amount, 0)
        self.assertEqual(vs[1].amount, 200)

    def test_both_change(self):
        """量价同时变. 用 Laspeyres-like (P_p 算量, C_q 算价)."""
        vs = decompose_revenue(
            previous_qty=100, previous_price=10,
            current_qty=120, current_price=12,
        )
        # 量差 = 20 × 10 = 200
        # 价差 = 2 × 120 = 240
        # 总和 = 440
        # 验证: cur revenue 1440 - prev 1000 = 440 ✓
        self.assertEqual(vs[0].amount, 200)
        self.assertEqual(vs[1].amount, 240)

    def test_qty_decrease(self):
        vs = decompose_revenue(
            previous_qty=100, previous_price=10,
            current_qty=80, current_price=10,
        )
        # 量差 = -20 × 10 = -200
        self.assertEqual(vs[0].amount, -200)


# ═══════════════════════════════════════════════════════════════════
# Gross Profit 4 维分解 — reconciliation 恒等式
# ═══════════════════════════════════════════════════════════════════

class GrossProfitDecomposeTests(unittest.TestCase):
    def _decompose(self, **kwargs):
        return decompose_gross_profit(**kwargs)

    def test_no_change(self):
        gv = self._decompose(
            previous_qty=100, previous_price=10, previous_unit_cost=4,
            current_qty=100, current_price=10, current_unit_cost=4,
        )
        self.assertEqual(gv.previous_gp, 600)
        self.assertEqual(gv.current_gp, 600)
        self.assertEqual(gv.total_delta, 0)
        for v in gv.variances:
            self.assertEqual(v.amount, 0)

    def test_only_qty_up(self):
        """销量从 100 → 120, 其它不变."""
        gv = self._decompose(
            previous_qty=100, previous_price=10, previous_unit_cost=4,
            current_qty=120, current_price=10, current_unit_cost=4,
        )
        # prev GP = (10-4)*100 = 600; cur GP = 6*120 = 720; delta = 120
        # 量差 = ΔQ × P_unit_margin = 20 × 6 = 120
        # 其它都 0
        self.assertEqual(gv.total_delta, 120)
        self.assertEqual(gv.volume.amount, 120)
        self.assertEqual(gv.price.amount, 0)
        self.assertEqual(gv.cost.amount, 0)
        self.assertEqual(gv.mix.amount, 0)

    def test_only_price_up(self):
        """单价 10 → 12, 量/成本不变."""
        gv = self._decompose(
            previous_qty=100, previous_price=10, previous_unit_cost=4,
            current_qty=100, current_price=12, current_unit_cost=4,
        )
        # 价差 = ΔP × C_qty = 2 × 100 = 200
        self.assertEqual(gv.total_delta, 200)
        self.assertEqual(gv.price.amount, 200)
        self.assertEqual(gv.volume.amount, 0)
        self.assertEqual(gv.cost.amount, 0)

    def test_only_cost_up(self):
        """单位成本 4 → 5, 量/价不变. 毛利下降."""
        gv = self._decompose(
            previous_qty=100, previous_price=10, previous_unit_cost=4,
            current_qty=100, current_price=10, current_unit_cost=5,
        )
        # 成本差 = -ΔC × C_qty = -1 × 100 = -100 (成本上升 → 毛利下降)
        self.assertEqual(gv.total_delta, -100)
        self.assertEqual(gv.cost.amount, -100)
        self.assertEqual(gv.volume.amount, 0)
        self.assertEqual(gv.price.amount, 0)

    def test_all_change(self):
        """三维同时变, 验证 reconciles 恒等式."""
        gv = self._decompose(
            previous_qty=100, previous_price=10, previous_unit_cost=4,
            current_qty=120, current_price=12, current_unit_cost=5,
        )
        # prev GP = 600
        # cur GP = (12-5)*120 = 840
        # total = 240
        self.assertEqual(gv.total_delta, 240)
        # 验证 reconciles
        self.assertTrue(gv.reconciles())
        s = sum(v.amount for v in gv.variances)
        self.assertAlmostEqual(s, gv.total_delta)

    def test_pct_of_total(self):
        gv = self._decompose(
            previous_qty=100, previous_price=10, previous_unit_cost=4,
            current_qty=120, current_price=10, current_unit_cost=4,
        )
        # 只有量差贡献, 应该是 100%
        self.assertAlmostEqual(gv.volume.pct_of_total, 1.0)
        # cost / price / mix 都 0; pct_of_total 应该 0 (而非 None)
        self.assertEqual(gv.cost.pct_of_total, 0)

    def test_zero_total_pct_is_none(self):
        """total_delta = 0 时 pct_of_total 应该 None (避免除零)."""
        gv = self._decompose(
            previous_qty=100, previous_price=10, previous_unit_cost=4,
            current_qty=100, current_price=10, current_unit_cost=4,
        )
        for v in gv.variances:
            self.assertIsNone(v.pct_of_total)


class VarianceImmutabilityTests(unittest.TestCase):
    def test_frozen(self):
        v = Variance(name="x", amount=100)
        with self.assertRaises(Exception):
            v.amount = 999  # noqa

    def test_gross_profit_variance_frozen(self):
        gv = decompose_gross_profit(
            previous_qty=10, previous_price=5, previous_unit_cost=2,
            current_qty=10, current_price=5, current_unit_cost=2,
        )
        with self.assertRaises(Exception):
            gv.total_delta = 999  # noqa


# ═══════════════════════════════════════════════════════════════════
# 真实场景: 文档里那个 ¥-2,103 的例子
# ═══════════════════════════════════════════════════════════════════

class RealisticScenarioTests(unittest.TestCase):
    def test_doc_example(self):
        """模拟 architecture-evolution-roadmap.md P5 例子里的差异分解.

        2026-05 vs 2026-04:
          总差异 ¥-2,103.50
          量差 -450 (-3.2% 销量)
          价差 +120 (改价提价)
          成本差 -1,680 (食材成本上涨)
          结构差 -93

        我们用 single-SKU 不能完全 reproducce 4 维, 但验证 reconcile.
        """
        gv = decompose_gross_profit(
            previous_qty=1000, previous_price=10, previous_unit_cost=4,
            current_qty=968, current_price=10.12, current_unit_cost=5.74,
        )
        # 验证 reconciles
        self.assertTrue(gv.reconciles())
        # 总差异 = current_gp - previous_gp
        # prev_gp = 6 * 1000 = 6000
        # cur_gp = (10.12 - 5.74) * 968 = 4.38 * 968 ≈ 4239.84
        # delta ≈ -1760.16
        self.assertLess(gv.total_delta, 0)
        # 成本差应该是主要负贡献 (ΔC=1.74 大)
        self.assertLess(gv.cost.amount, gv.volume.amount)
        self.assertLess(gv.cost.amount, gv.price.amount)


if __name__ == "__main__":
    unittest.main()
