"""order_line 凭证账 CTE 的 SQL 渲染契约 (镜像 tests/test_sale_event.py 模式)。"""
import unittest

import tests._setup  # noqa: F401

from semantic.entities import order_line


def render(sql: str) -> str:
    return sql.format(project="p", dataset="d", start_ts=1, end_ts=2)


class TestOrderLineCte(unittest.TestCase):
    def setUp(self):
        self.sql = render(
            f"WITH {order_line.order_line_cte()} SELECT * FROM order_line")

    def test_three_table_join(self):
        self.assertIn("`p`.`d`.`ttpos_sale_order_product` sop", self.sql)
        self.assertIn("`p`.`d`.`ttpos_sale_order` so", self.sql)
        self.assertIn("`p`.`d`.`ttpos_sale_bill` sb", self.sql)

    def test_soft_delete_filters(self):
        # ttpos 软删约定: 三张表都要 delete_time = 0
        self.assertIn("so.delete_time = 0", self.sql)
        self.assertIn("sb.delete_time = 0", self.sql)
        self.assertIn("sop.delete_time = 0", self.sql)

    def test_only_completed_bills(self):
        self.assertIn("sb.status = 1", self.sql)

    def test_time_window_on_bill_finish_time(self):
        self.assertIn("sb.finish_time >= 1", self.sql)
        self.assertIn("sb.finish_time < 2", self.sql)

    def test_excludes_combo_child_rows(self):
        # product_type: 0=单品, 1=套餐父行, 2=套餐子行 (package_uuid=父行uuid).
        # 子行不排除 → 凭证账 qty 翻倍 (2026-05 实测 31.5% 匹配率的根因)
        self.assertIn("sop.product_type != 2", self.sql)

    def test_measures(self):
        self.assertIn("SUM(sop.num) AS voucher_qty", self.sql)
        self.assertIn("SUM(sop.sale_price * sop.num) AS voucher_gross", self.sql)
        self.assertIn("SUM(sop.total_price) AS voucher_net", self.sql)
        self.assertIn("SUM(sop.discount_fee) AS voucher_discount", self.sql)
        self.assertIn("GROUP BY item_uuid", self.sql)


if __name__ == "__main__":
    unittest.main()
