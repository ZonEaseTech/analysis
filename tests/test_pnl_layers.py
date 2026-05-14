"""P3.5a — semantic/aggregations/pnl_layers.py 测试.

跑法: venv/bin/python -m unittest tests.test_pnl_layers -v
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from semantic.aggregations.pnl_layers import (
    Confidence,
    PnlLayer,
    PnlStatement,
    aggregate_sales,
    build_pnl,
)
from semantic.resolvers import DictProvider, Resolver


# ═══════════════════════════════════════════════════════════════════
# aggregate_sales
# ═══════════════════════════════════════════════════════════════════

class AggregateSalesTests(unittest.TestCase):
    def test_empty_rows(self):
        out = aggregate_sales([])
        # 所有 metric 都 0
        self.assertEqual(out["sales_price"], 0.0)
        self.assertEqual(out["actual_amount"], 0.0)
        self.assertEqual(out["qty"], 0.0)

    def test_dict_rows(self):
        rows = [
            {"sales_price": 100, "actual_amount": 80, "qty": 10},
            {"sales_price": 50, "actual_amount": 50, "qty": 5},
        ]
        out = aggregate_sales(rows)
        self.assertEqual(out["sales_price"], 150)
        self.assertEqual(out["actual_amount"], 130)
        self.assertEqual(out["qty"], 15)

    def test_attr_rows(self):
        rows = [SimpleNamespace(sales_price=100, qty=10)]
        out = aggregate_sales(rows)
        self.assertEqual(out["sales_price"], 100)

    def test_missing_field_counts_zero(self):
        rows = [{"sales_price": 100}]  # 没 qty
        out = aggregate_sales(rows)
        self.assertEqual(out["sales_price"], 100)
        self.assertEqual(out["qty"], 0)

    def test_none_values_safe(self):
        rows = [{"sales_price": None, "qty": 10}]
        out = aggregate_sales(rows)
        self.assertEqual(out["sales_price"], 0)
        self.assertEqual(out["qty"], 10)


# ═══════════════════════════════════════════════════════════════════
# build_pnl — 基础结构
# ═══════════════════════════════════════════════════════════════════

class BuildPnlStructureTests(unittest.TestCase):
    def test_returns_pnl_statement(self):
        pnl = build_pnl(period="2026-04", scope="全集团", sales_rows=[])
        self.assertIsInstance(pnl, PnlStatement)
        self.assertEqual(pnl.period, "2026-04")
        self.assertEqual(pnl.scope, "全集团")

    def test_all_standard_layers_present(self):
        """build_pnl 永远返回完整 23 层 (含 NOT_AVAILABLE 占位)."""
        pnl = build_pnl(period="2026-04", scope="x", sales_rows=[])
        codes = [layer.code for layer in pnl.layers]
        # 关键节点必须有
        for required in ["gmv", "net_sales", "gross_profit",
                         "contribution_margin", "operating_income"]:
            self.assertIn(required, codes)
        # 全部 23 层
        self.assertEqual(len(pnl.layers), 23)

    def test_subtotal_flag(self):
        pnl = build_pnl(period="x", scope="x", sales_rows=[])
        subtotal_codes = [l.code for l in pnl.layers if l.is_subtotal]
        self.assertEqual(
            subtotal_codes,
            ["gmv", "net_sales", "gross_profit",
             "contribution_margin", "operating_income"],
        )


# ═══════════════════════════════════════════════════════════════════
# build_pnl — 第 1 层金额 (GMV → Net Sales)
# ═══════════════════════════════════════════════════════════════════

class FirstLayerAmountsTests(unittest.TestCase):
    def setUp(self):
        # 一个典型 SKU 行: GMV 标价 100 + 50; 实收 80 + 50; 退款 20 等
        self.rows = [{
            "sales_price": 150,
            "dine_sales_price": 100,
            "takeout_sales_price": 50,
            "actual_amount": 130,
            "refund_amount": 5,
            "cancelled_amount": 0,
            "free_amount": 3,
            "give_amount": 2,
            "discount_amount": 10,
            "qty": 15, "dine_qty": 10, "takeout_qty": 5,
            "order_count": 10,
        }]

    def test_gmv(self):
        pnl = build_pnl(period="x", scope="x", sales_rows=self.rows)
        self.assertEqual(pnl.by_code("gmv").amount, 150)
        self.assertEqual(pnl.by_code("dine_gmv").amount, 100)
        self.assertEqual(pnl.by_code("takeout_gmv").amount, 50)

    def test_losses_are_negative(self):
        """减项金额是负数 (writer 直接 SUM 即可)."""
        pnl = build_pnl(period="x", scope="x", sales_rows=self.rows)
        self.assertEqual(pnl.by_code("returns_allowances").amount, -5)
        self.assertEqual(pnl.by_code("free_amount").amount, -3)
        self.assertEqual(pnl.by_code("give_amount").amount, -2)
        self.assertEqual(pnl.by_code("discount_amount").amount, -10)

    def test_net_sales_equals_actual_amount(self):
        """Net Sales = ttpos actual_sale_amount, 是对账锚."""
        pnl = build_pnl(period="x", scope="x", sales_rows=self.rows)
        self.assertEqual(pnl.by_code("net_sales").amount, 130)


# ═══════════════════════════════════════════════════════════════════
# build_pnl — COGS 与 Gross Profit
# ═══════════════════════════════════════════════════════════════════

class CogsAndGrossProfitTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"sales_price": 100, "actual_amount": 80}]

    def test_no_cogs_data(self):
        """没传 cogs_data, gross_profit 0, 标 NOT_AVAILABLE."""
        pnl = build_pnl(period="x", scope="x", sales_rows=self.rows)
        cogs_layer = pnl.by_code("cogs")
        gp_layer = pnl.by_code("gross_profit")
        self.assertEqual(cogs_layer.amount, 0.0)
        self.assertEqual(cogs_layer.confidence, Confidence.NOT_AVAILABLE)
        self.assertEqual(gp_layer.confidence, Confidence.NOT_AVAILABLE)

    def test_with_cogs_data(self):
        pnl = build_pnl(
            period="x", scope="x", sales_rows=self.rows,
            cogs_data={"total": 30, "dine": 20, "takeout": 10},
        )
        self.assertEqual(pnl.by_code("cogs").amount, -30)
        self.assertEqual(pnl.by_code("dine_cogs").amount, -20)
        self.assertEqual(pnl.by_code("takeout_cogs").amount, -10)
        # Gross Profit = Net Sales − COGS = 80 - 30 = 50
        self.assertEqual(pnl.by_code("gross_profit").amount, 50)
        self.assertEqual(pnl.by_code("gross_profit").confidence, Confidence.ACTUAL)


# ═══════════════════════════════════════════════════════════════════
# build_pnl — 平台抽佣 (P3 fact_overrides 接入)
# ═══════════════════════════════════════════════════════════════════

class PlatformCommissionTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{
            "sales_price": 100, "actual_amount": 90,
            "takeout_sales_price": 40,
        }]

    def test_no_resolver_marks_na(self):
        pnl = build_pnl(period="x", scope="x", sales_rows=self.rows,
                       cogs_data={"total": 20, "dine": 10, "takeout": 10})
        commission_layer = pnl.by_code("platform_commission")
        cm_layer = pnl.by_code("contribution_margin")
        self.assertEqual(commission_layer.amount, 0.0)
        self.assertEqual(commission_layer.confidence, Confidence.NOT_AVAILABLE)
        self.assertEqual(cm_layer.confidence, Confidence.NOT_AVAILABLE)

    def test_with_resolver_estimates(self):
        resolver = Resolver([
            DictProvider(name="defaults", priority=0,
                        data={"default": 0.25}),
        ])
        pnl = build_pnl(
            period="x", scope="x", sales_rows=self.rows,
            cogs_data={"total": 20, "dine": 10, "takeout": 10},
            commission_rate_resolver=resolver,
        )
        # platform_commission = takeout_gmv (40) × rate (0.25) = 10
        commission_layer = pnl.by_code("platform_commission")
        self.assertEqual(commission_layer.amount, -10)
        self.assertEqual(commission_layer.confidence, Confidence.ESTIMATED)
        # Contribution Margin = Gross Profit (70) − platform_commission (10) = 60
        # GP = Net Sales (90) − COGS (20) = 70
        self.assertEqual(pnl.by_code("contribution_margin").amount, 60)
        # source 写到 formula
        self.assertIn("defaults", commission_layer.formula)

    def test_resolver_no_match_falls_back_to_industry_default(self):
        """resolver 命中不了 default key, 用 25% 行业值兜底."""
        resolver = Resolver([
            DictProvider(name="x", priority=0, data={"grab": 0.30}),  # 没 default
        ])
        pnl = build_pnl(
            period="x", scope="x", sales_rows=self.rows,
            cogs_data={"total": 20, "dine": 10, "takeout": 10},
            commission_rate_resolver=resolver,
        )
        # 25% × 40 = 10
        self.assertEqual(pnl.by_code("platform_commission").amount, -10)
        self.assertIn("industry_default", pnl.by_code("platform_commission").formula)


# ═══════════════════════════════════════════════════════════════════
# PnlStatement 辅助方法
# ═══════════════════════════════════════════════════════════════════

class PnlStatementHelpersTests(unittest.TestCase):
    def test_by_code_missing(self):
        pnl = build_pnl(period="x", scope="x", sales_rows=[])
        self.assertIsNone(pnl.by_code("nonexistent"))

    def test_subtotal_amounts(self):
        rows = [{"sales_price": 100, "actual_amount": 80}]
        pnl = build_pnl(period="x", scope="x", sales_rows=rows,
                       cogs_data={"total": 20, "dine": 10, "takeout": 10})
        amounts = pnl.subtotal_amounts()
        self.assertEqual(amounts["gmv"], 100)
        self.assertEqual(amounts["net_sales"], 80)
        self.assertEqual(amounts["gross_profit"], 60)

    def test_all_amounts(self):
        pnl = build_pnl(period="x", scope="x", sales_rows=[])
        amounts = pnl.all_amounts()
        # 23 层都在
        self.assertEqual(len(amounts), 23)


# ═══════════════════════════════════════════════════════════════════
# PnlLayer 不可变
# ═══════════════════════════════════════════════════════════════════

class PnlLayerImmutabilityTests(unittest.TestCase):
    def test_frozen(self):
        l = PnlLayer(code="x", name_zh="X", name_en="X",
                    amount=100, confidence=Confidence.ACTUAL)
        with self.assertRaises(Exception):
            l.amount = 200  # noqa


if __name__ == "__main__":
    unittest.main()
