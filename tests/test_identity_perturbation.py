"""扰动测试:每条恒等式对它引用的每个字段必须敏感(可证伪性的机制保障)。

金额扰动用 +200(超过 _MUST_FIX_ABS=100),因为 A 阶段金额仍有容忍带;
PR-B 整数化后收紧为 +1 萨当。qty 扰动 +1(qty 零容差,立即可测)。
"""
import unittest

import tests._setup  # noqa: F401

from semantic.validators import check
from semantic.validators.core import Severity
from semantic.validators.identities import SALES_QTY_IDENTITY, AMOUNT_IDENTITY, GROSS_AMOUNT_IDENTITY


def _passing_sales_row():
    """一行满足全部销售恒等式的平衡数据。"""
    return {
        "qty": 100.0, "net_qty": 80.0, "free_qty": 5.0, "give_qty": 5.0,
        "refund_qty": 6.0, "cancelled_qty": 4.0,
        "sales_price": 1000.0, "revenue": 800.0, "refund_amount": 60.0,
        "free_amount": 50.0, "give_amount": 50.0, "discount_amount": 40.0,
        "cancelled_amount": 30.0,
        "gross_amount": 1030.0,  # = sales_price(1000) + cancelled_amount(30)
    }


QTY_DELTA = 1.0
MONEY_DELTA = 200.0  # > _MUST_FIX_ABS, 保证穿透 A 阶段容忍带
# cancelled_amount / gross_amount 是前向条目: GROSS_AMOUNT_IDENTITY (计划 Task 3)
# 的 fields 会引用它们, 金额路由必须就位. 新增金额恒等式时同步维护本集合 —
# 路由错了 test_every_field_perturbation_fires 会因扰动量不穿透容忍带而失败.
MONEY_FIELDS = {
    "sales_price", "revenue", "refund_amount", "free_amount",
    "give_amount", "discount_amount", "cancelled_amount", "gross_amount",
}


class TestIdentityPerturbation(unittest.TestCase):
    PERTURBABLE = [SALES_QTY_IDENTITY, AMOUNT_IDENTITY, GROSS_AMOUNT_IDENTITY]

    def test_identities_declare_fields(self):
        for ident in self.PERTURBABLE:
            self.assertTrue(ident.fields,
                            f"{ident.name} 缺 fields 元数据, 扰动测试无法覆盖它")

    def test_base_row_passes(self):
        result = check([_passing_sales_row()], self.PERTURBABLE)
        self.assertEqual(result.violations, [],
                         f"基准行必须全绿: {[(v.identity.name, v.delta) for v in result.violations]}")

    def test_every_field_perturbation_fires(self):
        for ident in self.PERTURBABLE:
            for f in ident.fields:
                row = _passing_sales_row()
                row[f] += MONEY_DELTA if f in MONEY_FIELDS else QTY_DELTA
                result = check([row], [ident])
                self.assertTrue(
                    result.violations,
                    f"{ident.name} 对字段 {f} 的扰动不敏感 — 恒等式不可证伪")


class TestSalesQtyIsDefinitional(unittest.TestCase):
    def test_derived_net_qty_makes_identity_vacuous(self):
        """特征化测试:net_qty 用减法推导时, 即使源数据离谱, SALES_QTY 照样通过.

        这就是 spec §1 问题 1 (循环恒等式). 真实检测力由 CROSS_LEDGER 提供 (Task 5).
        本测试存在的意义: 防止后人误信 SALES_QTY 有检测力 / 防止报表偷偷回到减法推导
        还宣称"过了校验".
        """
        raw = {"qty": 100.0, "free_qty": 5.0, "give_qty": 5.0,
               "refund_qty": 999.0, "cancelled_qty": 4.0}  # refund 离谱地错
        row = {
            **raw,
            "net_qty": raw["qty"] - raw["free_qty"] - raw["give_qty"]
                       - raw["refund_qty"] - raw["cancelled_qty"],
        }
        result = check([row], [SALES_QTY_IDENTITY])
        self.assertEqual(result.violations, [])  # 永真 — 文档化这个事实


if __name__ == "__main__":
    unittest.main()
