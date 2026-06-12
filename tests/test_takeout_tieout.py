"""takeout_tieout: 外卖订单级 platform_total vs item 级求和 互证 CTE + 恒等式。"""
import unittest

import tests._setup  # noqa: F401

from semantic.entities import takeout_tieout
from semantic.validators import check
from semantic.validators.core import Severity
from semantic.validators.identities import TAKEOUT_TIEOUT_IDENTITIES


def render(sql: str) -> str:
    return sql.format(project="p", dataset="d", start_ts=1, end_ts=2)


class TestTakeoutTieoutCte(unittest.TestCase):
    def setUp(self):
        self.sql = render(
            f"WITH {takeout_tieout.takeout_tieout_cte()} SELECT * FROM takeout_tieout")

    def test_dynamic_time_condition(self):
        # pitfalls §1.3: state=40 用 completed_time, 其余用 accepted_time
        self.assertIn("t.order_state = 40 AND t.completed_time >= 1", self.sql)
        self.assertIn("t.order_state != 40 AND t.accepted_time >= 1", self.sql)

    def test_order_grain_measures(self):
        self.assertIn("IFNULL(t.platform_total, 0) AS platform_total", self.sql)
        self.assertIn("IFNULL(t.merchant_charge_fee, 0) AS merchant_charge_fee", self.sql)
        self.assertIn("IFNULL(t.merchant_discount, 0) AS merchant_discount", self.sql)
        self.assertIn("SUM(toi.price * toi.quantity) AS item_sum", self.sql)

    def test_soft_delete(self):
        self.assertIn("t.delete_time = 0", self.sql)
        self.assertIn("toi.delete_time = 0", self.sql)


class TestTakeoutTieoutIdentities(unittest.TestCase):
    def _row(self, platform_total=100.0, item_sum=100.0, fee=0.0, disc=0.0):
        return {"order_uuid": "1", "platform_total": platform_total,
                "item_sum": item_sum, "merchant_charge_fee": fee,
                "merchant_discount": disc}

    def test_balanced_passes(self):
        result = check([self._row()], TAKEOUT_TIEOUT_IDENTITIES)
        self.assertEqual(result.violations, [])

    def test_drift_fires_capped_at_review(self):
        # 升级到 MUST_FIX 须等观察跑校准 (含 merchant 费用符号) — 先封顶 🟡
        result = check([self._row(item_sum=300.0)], TAKEOUT_TIEOUT_IDENTITIES)
        self.assertTrue(result.violations)
        self.assertTrue(all(v.severity == Severity.NEEDS_REVIEW
                            for v in result.violations))

    def test_nonzero_merchant_fee_fires(self):
        # 华莱士当前 fee=0; 业务开启费用即 fire, 提醒校准口径 (pitfalls §5.1)
        result = check([self._row(fee=5.0)], TAKEOUT_TIEOUT_IDENTITIES)
        self.assertTrue(result.violations)


if __name__ == "__main__":
    unittest.main()
