"""扰动测试:每条恒等式对它引用的每个字段必须敏感(可证伪性的机制保障)。

PR-B 7c 整数化后:金额是萨当整数, sum 型恒等式 (AMOUNT/GROSS) 零容差 —
扰动 +1 萨当即 fire(_exact_satang_classify, delta==0 才放行)。
qty 扰动 +1(qty 零容差,立即可测)。fixture 金额值 = 元 × 100。
"""
import unittest

import tests._setup  # noqa: F401

from semantic.validators import check
from semantic.validators.core import Severity
from semantic.validators.identities import SALES_QTY_IDENTITY, AMOUNT_IDENTITY, GROSS_AMOUNT_IDENTITY


def _passing_sales_row():
    """一行满足全部销售恒等式的平衡数据 (金额是萨当整数, = 元 × 100)。"""
    return {
        "qty": 100.0, "net_qty": 80.0, "free_qty": 5.0, "give_qty": 5.0,
        "refund_qty": 6.0, "cancelled_qty": 4.0,
        "sales_price": 100000, "revenue": 80000, "refund_amount": 6000,
        "free_amount": 5000, "give_amount": 5000, "discount_amount": 4000,
        "cancelled_amount": 3000,
        "gross_amount": 103000,  # = sales_price(100000) + cancelled_amount(3000)
    }


QTY_DELTA = 1.0
MONEY_DELTA = 1.0  # 1 萨当 — 零容差的证明 (PR-B 7c: _exact_satang_classify)
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
