"""profit_by_price 的自动版本号。

规则: 每次新导出都 _v{N+1}.xlsx，N 自动递增。不做内容去重。
"""
import tempfile
import unittest
from pathlib import Path

from tests import _setup  # noqa: F401

from bq_reports.profit_by_price_report import _next_version_path


class NextVersionPathTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.prefix = "report_202604"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_first_call_returns_v1(self):
        p = _next_version_path(self.tmpdir, self.prefix)
        self.assertEqual(p.name, "report_202604_v1.xlsx")

    def test_picks_max_plus_one(self):
        # 既有 v1, v2, v3 → 下一个 v4
        for v in (1, 2, 3):
            (self.tmpdir / f"report_202604_v{v}.xlsx").write_bytes(b"x")
        p = _next_version_path(self.tmpdir, self.prefix)
        self.assertEqual(p.name, "report_202604_v4.xlsx")

    def test_picks_max_even_with_gaps(self):
        """v1, v3 缺 v2 → 下一个 v4，不填洞。"""
        (self.tmpdir / "report_202604_v1.xlsx").write_bytes(b"a")
        (self.tmpdir / "report_202604_v3.xlsx").write_bytes(b"c")
        p = _next_version_path(self.tmpdir, self.prefix)
        self.assertEqual(p.name, "report_202604_v4.xlsx")

    def test_ignores_unrelated_files(self):
        """别的前缀 / 隐藏文件 / 没 _vN 的文件 → 不影响版本号判定。"""
        (self.tmpdir / "report_202604.xlsx").write_bytes(b"x")          # 没 vN
        (self.tmpdir / "report_OTHER_v9.xlsx").write_bytes(b"x")       # 别的前缀
        (self.tmpdir / ".report_202604_tmp_v5.xlsx").write_bytes(b"x")  # 隐藏 + 不匹配前缀
        p = _next_version_path(self.tmpdir, self.prefix)
        self.assertEqual(p.name, "report_202604_v1.xlsx")

    def test_returns_double_digit_version(self):
        for v in (1, 5, 9, 10, 11, 12):
            (self.tmpdir / f"report_202604_v{v}.xlsx").write_bytes(b"x")
        p = _next_version_path(self.tmpdir, self.prefix)
        self.assertEqual(p.name, "report_202604_v13.xlsx")


if __name__ == "__main__":
    unittest.main()
