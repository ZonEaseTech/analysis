"""P3.5a — semantic/aggregations/kpi_ratios.py 测试.

跑法: venv/bin/python -m unittest tests.test_kpi_ratios -v
"""
from __future__ import annotations

import unittest

from semantic.aggregations.kpi_ratios import (
    Benchmark,
    HealthStatus,
    INDUSTRY_BENCHMARKS,
    Kpi,
    compute_kpis,
)


def _kpi_by_code(kpis, code):
    for k in kpis:
        if k.code == code:
            return k
    return None


# ═══════════════════════════════════════════════════════════════════
# INDUSTRY_BENCHMARKS 完整性
# ═══════════════════════════════════════════════════════════════════

class BenchmarksTests(unittest.TestCase):
    def test_all_required_benchmarks_present(self):
        for required in ["gross_margin", "food_cost", "labor_cost",
                         "prime_cost", "operating_margin", "effective_take_rate"]:
            self.assertIn(required, INDUSTRY_BENCHMARKS)

    def test_directions_valid(self):
        for k, b in INDUSTRY_BENCHMARKS.items():
            self.assertIn(b.direction, ("higher_better", "lower_better"),
                         msg=f"{k}")

    def test_bands_sane(self):
        for k, b in INDUSTRY_BENCHMARKS.items():
            self.assertLess(b.healthy_low, b.healthy_high, msg=f"{k}")


# ═══════════════════════════════════════════════════════════════════
# compute_kpis — 利润率
# ═══════════════════════════════════════════════════════════════════

class GrossMarginTests(unittest.TestCase):
    def _compute(self, net_sales, gross_profit):
        return compute_kpis(
            pnl_amounts={"net_sales": net_sales, "gross_profit": gross_profit,
                        "cogs": -(net_sales - gross_profit)},
            sales_totals={},
        )

    def test_healthy_65pct(self):
        kpis = self._compute(net_sales=100, gross_profit=65)
        gm = _kpi_by_code(kpis, "gross_margin")
        self.assertAlmostEqual(gm.value, 0.65)
        self.assertEqual(gm.health, HealthStatus.HEALTHY)

    def test_warning_55pct(self):
        kpis = self._compute(net_sales=100, gross_profit=55)
        gm = _kpi_by_code(kpis, "gross_margin")
        self.assertEqual(gm.health, HealthStatus.WARNING)

    def test_critical_40pct(self):
        kpis = self._compute(net_sales=100, gross_profit=40)
        gm = _kpi_by_code(kpis, "gross_margin")
        self.assertEqual(gm.health, HealthStatus.CRITICAL)

    def test_zero_net_sales_returns_na(self):
        kpis = self._compute(net_sales=0, gross_profit=0)
        gm = _kpi_by_code(kpis, "gross_margin")
        self.assertIsNone(gm.value)
        self.assertEqual(gm.health, HealthStatus.NOT_AVAILABLE)


class FoodCostTests(unittest.TestCase):
    def test_in_band_30pct(self):
        kpis = compute_kpis(
            pnl_amounts={"net_sales": 100, "cogs": -30},
            sales_totals={},
        )
        fc = _kpi_by_code(kpis, "food_cost")
        self.assertAlmostEqual(fc.value, 0.30)
        self.assertEqual(fc.health, HealthStatus.HEALTHY)

    def test_too_high_50pct_critical(self):
        kpis = compute_kpis(
            pnl_amounts={"net_sales": 100, "cogs": -50},
            sales_totals={},
        )
        fc = _kpi_by_code(kpis, "food_cost")
        self.assertEqual(fc.health, HealthStatus.CRITICAL)

    def test_no_cogs_na(self):
        kpis = compute_kpis(
            pnl_amounts={"net_sales": 100, "cogs": 0},
            sales_totals={},
        )
        fc = _kpi_by_code(kpis, "food_cost")
        self.assertEqual(fc.value, 0.0)
        # food_cost = 0 / 100 = 0 是 acceptable (低于 28%)
        self.assertEqual(fc.health, HealthStatus.ACCEPTABLE)


class LaborAndPrimeCostTests(unittest.TestCase):
    def test_no_labor_data_na(self):
        kpis = compute_kpis(
            pnl_amounts={"net_sales": 100, "cogs": -30},
            sales_totals={},
            labor_cost=None,
        )
        labor = _kpi_by_code(kpis, "labor_cost")
        prime = _kpi_by_code(kpis, "prime_cost")
        self.assertIsNone(labor.value)
        self.assertIsNone(prime.value)

    def test_with_labor_data(self):
        kpis = compute_kpis(
            pnl_amounts={"net_sales": 100, "cogs": -30, "gross_profit": 70},
            sales_totals={},
            labor_cost=28,
        )
        labor = _kpi_by_code(kpis, "labor_cost")
        prime = _kpi_by_code(kpis, "prime_cost")
        self.assertAlmostEqual(labor.value, 0.28)
        self.assertEqual(labor.health, HealthStatus.HEALTHY)
        self.assertAlmostEqual(prime.value, 0.58)  # (30+28)/100
        self.assertEqual(prime.health, HealthStatus.HEALTHY)


# ═══════════════════════════════════════════════════════════════════
# AOV
# ═══════════════════════════════════════════════════════════════════

class AovTests(unittest.TestCase):
    def test_aov_basic(self):
        kpis = compute_kpis(
            pnl_amounts={"net_sales": 1000},
            sales_totals={"order_count": 10},
        )
        aov = _kpi_by_code(kpis, "aov")
        self.assertEqual(aov.value, 100.0)

    def test_aov_zero_orders_na(self):
        kpis = compute_kpis(
            pnl_amounts={"net_sales": 1000},
            sales_totals={"order_count": 0},
        )
        aov = _kpi_by_code(kpis, "aov")
        self.assertIsNone(aov.value)
        self.assertIn("order_count", aov.note)


# ═══════════════════════════════════════════════════════════════════
# Channel Mix
# ═══════════════════════════════════════════════════════════════════

class ChannelMixTests(unittest.TestCase):
    def test_dine_takeout_mix(self):
        kpis = compute_kpis(
            pnl_amounts={"net_sales": 1000},
            sales_totals={
                "dine_sales_price": 700,
                "takeout_sales_price": 300,
            },
        )
        dine = _kpi_by_code(kpis, "dine_mix")
        take = _kpi_by_code(kpis, "takeout_mix")
        self.assertAlmostEqual(dine.value, 0.70)
        self.assertAlmostEqual(take.value, 0.30)

    def test_zero_sales_no_mix_emitted(self):
        kpis = compute_kpis(
            pnl_amounts={"net_sales": 0},
            sales_totals={"dine_sales_price": 0, "takeout_sales_price": 0},
        )
        self.assertIsNone(_kpi_by_code(kpis, "dine_mix"))
        self.assertIsNone(_kpi_by_code(kpis, "takeout_mix"))


# ═══════════════════════════════════════════════════════════════════
# Effective Take Rate
# ═══════════════════════════════════════════════════════════════════

class TakeRateTests(unittest.TestCase):
    def test_take_rate_emitted(self):
        kpis = compute_kpis(
            pnl_amounts={
                "net_sales": 1000,
                "platform_commission": -100,  # 注意是负值 (减项)
            },
            sales_totals={
                "dine_sales_price": 700,
                "takeout_sales_price": 300,
            },
        )
        tr = _kpi_by_code(kpis, "effective_take_rate")
        # 100 / 300 = 33.3%
        self.assertAlmostEqual(tr.value, 100 / 300, places=3)
        # 高于 30% → CRITICAL? 等于 30% 边界 → 取决于实现; 33.3% 应该 WARNING
        # 行业 20-30% lower_better
        self.assertIn(tr.health, (HealthStatus.WARNING, HealthStatus.CRITICAL))

    def test_no_commission_not_emitted(self):
        kpis = compute_kpis(
            pnl_amounts={"net_sales": 1000},
            sales_totals={"dine_sales_price": 700, "takeout_sales_price": 300},
        )
        self.assertIsNone(_kpi_by_code(kpis, "effective_take_rate"))


# ═══════════════════════════════════════════════════════════════════
# Kpi 不可变
# ═══════════════════════════════════════════════════════════════════

class KpiImmutabilityTests(unittest.TestCase):
    def test_frozen(self):
        k = Kpi(code="x", name_zh="X", name_en="X", value=0.5, format="percent")
        with self.assertRaises(Exception):
            k.value = 0.6  # noqa


if __name__ == "__main__":
    unittest.main()
