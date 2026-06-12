"""结构性覆盖: 报表脚本必须接导出闸门 (CLAUDE.md 第 3 节"无例外"的机制化)。

名单已清空 (Task 11), 全部脚本无例外接入闸门.
闸门证据 = 直调 validate_and_gate 或经 GateSpec 走集中式钩子.
"""
import ast
import unittest
from pathlib import Path

import tests._setup  # noqa: F401
from tests._setup import REPO_ROOT


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

    def test_wired_scripts_use_gate(self):
        """全部脚本无例外: 每个 bq_reports/*.py 必须接导出闸门."""
        for p in self._report_scripts():
            self.assertTrue(_uses_gate(p),
                            f"{p.name} 必须接导出闸门 (validate_and_gate) — "
                            f"CLAUDE.md 第 3 节, 无例外")


if __name__ == "__main__":
    unittest.main()
