"""
Task 1.1: 价表默认改为 Buying - Internal，去掉静默 last_purchase_rate 回退。

对齐 ttpos main/app/service/business_cost_profit_erp_cost.go:103
priceList='Buying - Internal'

stdlib unittest（仓库约定，无需 pip install）。
"""
import os
import unittest
from unittest import mock

from tests import _setup  # noqa: F401

from bq_reports.utils import erpnext_api as E


class TestPriceListDefault(unittest.TestCase):
    def test_default_price_list_is_buying_internal(self):
        # 不传 price_list 且 env 未设时,默认应为 ttpos 成本毛利口径价表
        # 对齐 ttpos business_cost_profit_erp_cost.go:103 priceList='Buying - Internal'
        self.assertEqual(E.COST_PROFIT_PRICE_LIST, "Buying - Internal")

    def test_last_purchase_fallback_is_not_silent(self):
        # 静默把 last_purchase_rate 当 Item Price 会引入隐性口径漂移:
        # 必须显式开关,默认关闭
        self.assertFalse(E.ALLOW_LAST_PURCHASE_FALLBACK_DEFAULT)


class TestLastPurchaseRaiseWithoutFlag(unittest.TestCase):
    def setUp(self):
        # 确保 env 里有 ERPNEXT_PRICE_SOURCE=last_purchase_rate
        self._orig_env = os.environ.get("ERPNEXT_PRICE_SOURCE")
        os.environ["ERPNEXT_PRICE_SOURCE"] = "last_purchase_rate"

    def tearDown(self):
        if self._orig_env is None:
            os.environ.pop("ERPNEXT_PRICE_SOURCE", None)
        else:
            os.environ["ERPNEXT_PRICE_SOURCE"] = self._orig_env

    def test_raises_when_allow_last_purchase_not_set(self):
        # 默认 allow_last_purchase=False → 必须抛 RuntimeError，不能静默
        with self.assertRaises(RuntimeError) as ctx:
            E.load_erpnext_prices()
        self.assertIn("last_purchase_rate", str(ctx.exception))
        self.assertIn("allow_last_purchase=True", str(ctx.exception))

    def test_allow_last_purchase_true_calls_item_api(self):
        # 显式开启时才走 load_erpnext_item_last_purchase
        fake = {"ITEM_A": (12.5, "g")}
        with mock.patch.object(E, "load_erpnext_item_last_purchase", return_value=fake) as m:
            result = E.load_erpnext_prices(allow_last_purchase=True)
        m.assert_called_once()
        self.assertEqual(result, fake)


class TestDefaultPriceListUsedInApi(unittest.TestCase):
    def setUp(self):
        # 清掉可能影响的 env
        self._orig_source = os.environ.pop("ERPNEXT_PRICE_SOURCE", None)
        self._orig_list = os.environ.pop("ERPNEXT_PRICE_LIST", None)

    def tearDown(self):
        if self._orig_source is not None:
            os.environ["ERPNEXT_PRICE_SOURCE"] = self._orig_source
        if self._orig_list is not None:
            os.environ["ERPNEXT_PRICE_LIST"] = self._orig_list

    def test_no_env_uses_cost_profit_price_list_constant(self):
        # 不传 price_list、env 中无 ERPNEXT_PRICE_LIST 时
        # 最终传给 _api_get 的 price_list 应为 COST_PROFIT_PRICE_LIST
        captured = {}

        def fake_api_get(_base, _auth, doctype, fields, filters=None, limit=0):
            # 从 filters 里抓 price_list 值
            for f in (filters or []):
                if f[0] == "price_list":
                    captured["price_list"] = f[2]
            return []

        with mock.patch.object(E, "_get_auth", return_value=("http://fake", "fake_sid")), \
             mock.patch.object(E, "_api_get", side_effect=fake_api_get):
            # dotenv load_dotenv 不会真正加载（fake env），所以跳过
            with mock.patch("dotenv.load_dotenv"):
                E.load_erpnext_prices()

        self.assertEqual(
            captured.get("price_list"), E.COST_PROFIT_PRICE_LIST,
            f"期望 {E.COST_PROFIT_PRICE_LIST!r}，实际传给 API 的是 {captured.get('price_list')!r}",
        )


class TestPickItemPriceRow(unittest.TestCase):
    """
    Task 1.2: 测试 _pick_item_price_row 对齐 ttpos preferItemUnitCost 选行逻辑。
    ttpos-bmp: .../logic/stock/item.go:385-398
    """

    def test_pick_row_matching_desired_uom(self):
        # 同一物料有 g 与 ctn 两行价,desired='g' 必须取 g 行(对齐 ttpos preferItemUnitCost)
        rows = [
            {"item_code": "X", "uom": "ctn", "price_list_rate": 300, "modified": "2026-06-02"},
            {"item_code": "X", "uom": "g", "price_list_rate": 0.3, "modified": "2026-06-01"},
        ]
        picked = E._pick_item_price_row(rows, desired_uom="g")
        self.assertEqual(picked["uom"], "g")
        self.assertAlmostEqual(picked["price_list_rate"], 0.3)

    def test_no_desired_uom_match_returns_flagged_none(self):
        # desired_uom 指定了但无对应行 → 返回 None,显式暴露缺口
        rows = [{"item_code": "X", "uom": "ctn", "price_list_rate": 300, "modified": "2026-06-02"}]
        self.assertIsNone(E._pick_item_price_row(rows, desired_uom="g"))

    def test_pick_row_desired_uom_case_insensitive(self):
        # UOM 大小写不敏感（ERP 数据可能有 "G"、"Nos" 等混写）
        rows = [{"item_code": "Y", "uom": "G", "price_list_rate": 0.5, "modified": "2026-06-01"}]
        picked = E._pick_item_price_row(rows, desired_uom="g")
        self.assertIsNotNone(picked)
        self.assertAlmostEqual(picked["price_list_rate"], 0.5)

    def test_pick_row_desired_uom_multiple_matches_takes_latest_modified(self):
        # 同 UOM 多行 → 取 modified 最新的一行
        rows = [
            {"item_code": "Z", "uom": "g", "price_list_rate": 1.0, "modified": "2026-05-01"},
            {"item_code": "Z", "uom": "g", "price_list_rate": 1.5, "modified": "2026-06-10"},
        ]
        picked = E._pick_item_price_row(rows, desired_uom="g")
        self.assertAlmostEqual(picked["price_list_rate"], 1.5)

    def test_pick_row_no_desired_falls_back_to_uom_priority(self):
        # 无 desired_uom → 回退 UOM_PRIORITY（g 优先于 ctn）
        rows = [
            {"item_code": "A", "uom": "ctn", "price_list_rate": 300, "modified": "2026-06-02"},
            {"item_code": "A", "uom": "g", "price_list_rate": 0.3, "modified": "2026-06-01"},
        ]
        picked = E._pick_item_price_row(rows, desired_uom=None)
        self.assertEqual(picked["uom"], "g")

    def test_pick_row_empty_rows_returns_none(self):
        # 空 rows → 返回 None
        self.assertIsNone(E._pick_item_price_row([]))


class TestLoadErpnextPricesDesiredUoms(unittest.TestCase):
    """
    Task 1.2: load_erpnext_prices 的 desired_uoms 参数测试。
    """

    def _make_api_rows(self):
        return [
            {"item_code": "MAT_A", "uom": "g", "price_list_rate": 0.3, "modified": "2026-06-01"},
            {"item_code": "MAT_A", "uom": "ctn", "price_list_rate": 300, "modified": "2026-06-02"},
            {"item_code": "MAT_B", "uom": "pc", "price_list_rate": 5.0, "modified": "2026-06-01"},
        ]

    def test_desired_uoms_selects_correct_row(self):
        # desired_uoms 指定了 MAT_A->g，结果应取 g 行
        with mock.patch.object(E, "_get_auth", return_value=("http://fake", "sid")), \
             mock.patch.object(E, "_api_get", return_value=self._make_api_rows()), \
             mock.patch("dotenv.load_dotenv"):
            result = E.load_erpnext_prices(desired_uoms={"MAT_A": "g", "MAT_B": "pc"})
        self.assertIn("MAT_A", result)
        rate, uom = result["MAT_A"]
        self.assertEqual(uom, "g")
        self.assertAlmostEqual(rate, 0.3)

    def test_desired_uoms_missing_uom_excludes_item(self):
        # desired_uoms 指定 MAT_A->kg，但无 kg 行 → MAT_A 不进结果（显式暴露缺口）
        with mock.patch.object(E, "_get_auth", return_value=("http://fake", "sid")), \
             mock.patch.object(E, "_api_get", return_value=self._make_api_rows()), \
             mock.patch("dotenv.load_dotenv"):
            result = E.load_erpnext_prices(desired_uoms={"MAT_A": "kg"})
        self.assertNotIn("MAT_A", result)

    def test_no_desired_uoms_uses_legacy_priority(self):
        # 不传 desired_uoms → 行为与 Task 1.1 前相同（g 优先于 ctn）
        with mock.patch.object(E, "_get_auth", return_value=("http://fake", "sid")), \
             mock.patch.object(E, "_api_get", return_value=self._make_api_rows()), \
             mock.patch("dotenv.load_dotenv"):
            result = E.load_erpnext_prices()
        # MAT_A 有 g 和 ctn，应选 g（优先级更高）
        self.assertIn("MAT_A", result)
        _, uom = result["MAT_A"]
        self.assertEqual(uom, "g")


if __name__ == "__main__":
    unittest.main()
