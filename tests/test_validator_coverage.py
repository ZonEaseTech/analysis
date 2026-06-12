"""结构性覆盖: 报表脚本必须接导出闸门 (CLAUDE.md 第 3 节"无例外"的机制化)。

PENDING 是未接脚本的收缩名单 — 只许删不许加. 新脚本不接闸门, 本测试直接挂. 闸门证据 = 直调 validate_and_gate 或经 GateSpec 走集中式钩子.
"""
import ast
import unittest
from pathlib import Path

import tests._setup  # noqa: F401
from tests._setup import REPO_ROOT

PENDING = set()  # 全部脚本已接闸门 — Task 11 完成


def _uses_gate(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and \
                "semantic.validators" in node.module:
            if any(a.name in ("validate_and_gate", "gate", "GateSpec") for a in node.names):
                return True
        if isinstance(node, ast.Name) and node.id == "validate_and_gate":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "validate_and_gate":
            return True
        if isinstance(node, ast.Name) and node.id == "GateSpec":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "GateSpec":
            return True
    return False


class TestValidatorCoverage(unittest.TestCase):
    def _report_scripts(self):
        return sorted(p for p in (REPO_ROOT / "bq_reports").glob("*.py")
                      if p.name != "__init__.py")

    def test_inventory_matches(self):
        """脚本清单变动 (新增/删除) 必须显式过本测试 — 防 PENDING 名单失效."""
        names = {p.name for p in self._report_scripts()}
        unknown = PENDING - names
        self.assertFalse(unknown, f"PENDING 里有不存在的脚本: {unknown}")

    def test_wired_scripts_use_gate(self):
        for p in self._report_scripts():
            if p.name in PENDING:
                continue
            self.assertTrue(_uses_gate(p),
                            f"{p.name} 必须接导出闸门 (validate_and_gate) — "
                            f"CLAUDE.md 第 3 节, 无例外")

    def test_pending_scripts_actually_pending(self):
        """防名单过期: 已接的脚本必须从 PENDING 删掉."""
        for p in self._report_scripts():
            if p.name in PENDING:
                self.assertFalse(_uses_gate(p),
                                 f"{p.name} 已接闸门, 从 PENDING 删除它")


if __name__ == "__main__":
    unittest.main()
