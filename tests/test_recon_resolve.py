"""锁死 resolve_recon_path（原 BASE 未定义 bug 的回归网）。"""
import os
import sys
import tempfile
import unittest

from tests import _setup  # noqa: F401  sys.path bootstrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bom_pipeline"))
from wallace_bom_margin import resolve_recon_path  # noqa: E402


class TestResolveReconPath(unittest.TestCase):
    def test_explicit_path_wins(self):
        self.assertEqual(resolve_recon_path("/any/sales_2026-05.xlsx", "/x/r.json"), "/x/r.json")

    def test_finds_recon_json_sibling_without_month(self):
        # sales 文件名无月份也能找到同目录 recon.json（原 bug 场景）
        with tempfile.TemporaryDirectory() as d:
            sales = os.path.join(d, "sales_fixed.xlsx")
            recon = os.path.join(d, "recon.json")
            open(sales, "w").close()
            open(recon, "w").close()
            self.assertEqual(resolve_recon_path(sales), recon)

    def test_finds_month_named_sibling(self):
        with tempfile.TemporaryDirectory() as d:
            sales = os.path.join(d, "Wallace全店_..._2026-05_已修正.xlsx")
            recon = os.path.join(d, "Wallace门店实收明细_2026-05.json")
            open(sales, "w").close()
            open(recon, "w").close()
            self.assertEqual(resolve_recon_path(sales), recon)

    def test_returns_none_not_nameerror(self):
        # 原 BASE 未定义会抛 NameError；修复后应安全返回 None
        with tempfile.TemporaryDirectory() as d:
            sales = os.path.join(d, "sales_fixed.xlsx")
            open(sales, "w").close()
            self.assertIsNone(resolve_recon_path(sales))


if __name__ == "__main__":
    unittest.main()
