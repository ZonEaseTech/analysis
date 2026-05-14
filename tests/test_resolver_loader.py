"""P3 — load_resolvers_from_yaml 测试。

跑法: venv/bin/python -m unittest tests.test_resolver_loader -v
"""
from __future__ import annotations

import os
import tempfile
import textwrap
import unittest

from semantic.resolvers import load_resolvers_from_yaml, Resolver


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def _write_yaml(content: str) -> str:
    """写临时 yaml 文件返回路径。tearDown 不自动清理 (tempfile 系统会清)。"""
    fd, path = tempfile.mkstemp(suffix=".yaml")
    os.write(fd, content.encode("utf-8"))
    os.close(fd)
    return path


# ═══════════════════════════════════════════════════════════════════
# 基础加载
# ═══════════════════════════════════════════════════════════════════

class BasicLoadTests(unittest.TestCase):
    def test_missing_yaml_returns_empty_dict(self):
        """yaml 不存在时返回 {} (报表能正常跑, 走默认逻辑)."""
        out = load_resolvers_from_yaml(
            "/non/existent/path.yaml",
            allowed_categories=["x"],
        )
        self.assertEqual(out, {})

    def test_empty_yaml(self):
        path = _write_yaml("")
        out = load_resolvers_from_yaml(path, allowed_categories=["x"])
        self.assertEqual(out, {})

    def test_yaml_without_resolvers_key(self):
        path = _write_yaml("other_section:\n  foo: bar\n")
        out = load_resolvers_from_yaml(path, allowed_categories=["x"])
        self.assertEqual(out, {})

    def test_single_category_dict_kind(self):
        path = _write_yaml(textwrap.dedent("""
            resolvers:
              commission_rate:
                - kind: dict
                  name: defaults
                  priority: 0
                  data:
                    grab: 0.30
                    lineman: 0.25
        """).strip())

        out = load_resolvers_from_yaml(path, allowed_categories=["commission_rate"])
        self.assertIn("commission_rate", out)
        r = out["commission_rate"]
        self.assertIsInstance(r, Resolver)

        grab = r.resolve("grab")
        self.assertEqual(grab.value, 0.30)
        self.assertEqual(grab.source, "defaults")
        self.assertEqual(grab.priority, 0)


# ═══════════════════════════════════════════════════════════════════
# 白名单过滤
# ═══════════════════════════════════════════════════════════════════

class WhitelistTests(unittest.TestCase):
    def test_unauthorized_category_silently_skipped(self):
        """不在白名单的 category 静默忽略 (不抛错避免新业务接入炸)."""
        path = _write_yaml(textwrap.dedent("""
            resolvers:
              commission_rate:
                - kind: dict
                  name: a
                  priority: 0
                  data: {grab: 0.30}
              labor_cost:
                - kind: dict
                  name: b
                  priority: 0
                  data: {fixed: 100000}
        """).strip())

        out = load_resolvers_from_yaml(path, allowed_categories=["commission_rate"])
        self.assertIn("commission_rate", out)
        self.assertNotIn("labor_cost", out)  # 白名单过滤

    def test_empty_whitelist_loads_nothing(self):
        path = _write_yaml(textwrap.dedent("""
            resolvers:
              x: [{kind: dict, name: a, priority: 0, data: {k: 1}}]
        """).strip())

        out = load_resolvers_from_yaml(path, allowed_categories=[])
        self.assertEqual(out, {})


# ═══════════════════════════════════════════════════════════════════
# Priority + 多 source
# ═══════════════════════════════════════════════════════════════════

class PriorityStackTests(unittest.TestCase):
    def test_multi_priority_stack(self):
        path = _write_yaml(textwrap.dedent("""
            resolvers:
              commission_rate:
                - kind: dict
                  name: low
                  priority: 10
                  data: {grab: 0.50}
                - kind: dict
                  name: high
                  priority: 100
                  data: {grab: 0.30}
        """).strip())

        out = load_resolvers_from_yaml(path, allowed_categories=["commission_rate"])
        r = out["commission_rate"]

        grab = r.resolve("grab")
        # 高 priority 赢 (无论 yaml 里顺序如何 — Resolver 自动重排)
        self.assertEqual(grab.value, 0.30)
        self.assertEqual(grab.source, "high")

    def test_fallback_to_lower_priority(self):
        path = _write_yaml(textwrap.dedent("""
            resolvers:
              commission_rate:
                - kind: dict
                  name: high
                  priority: 100
                  data: {grab: 0.30}
                - kind: dict
                  name: low
                  priority: 0
                  data: {lineman: 0.25}
        """).strip())

        out = load_resolvers_from_yaml(path, allowed_categories=["commission_rate"])
        r = out["commission_rate"]
        # grab 在 high 命中
        self.assertEqual(r.resolve("grab").source, "high")
        # lineman 在 low 命中
        self.assertEqual(r.resolve("lineman").source, "low")
        # shopee 都没有
        self.assertIsNone(r.resolve("shopee"))


# ═══════════════════════════════════════════════════════════════════
# Callable kind (通过 fetchers 命名引用)
# ═══════════════════════════════════════════════════════════════════

class CallableKindTests(unittest.TestCase):
    def test_callable_via_fetchers(self):
        path = _write_yaml(textwrap.dedent("""
            resolvers:
              dynamic_lookup:
                - kind: callable
                  name: api
                  priority: 50
                  fetch: my_api_fetch
        """).strip())

        call_log = []

        def my_api_fetch(key):
            call_log.append(key)
            return {"A": 1, "B": 2}.get(key)

        out = load_resolvers_from_yaml(
            path,
            allowed_categories=["dynamic_lookup"],
            fetchers={"my_api_fetch": my_api_fetch},
        )
        r = out["dynamic_lookup"]

        result_a = r.resolve("A")
        self.assertEqual(result_a.value, 1)
        self.assertEqual(result_a.source, "api")
        self.assertIsNone(r.resolve("Z"))
        self.assertEqual(call_log, ["A", "Z"])

    def test_callable_missing_fetcher_raises(self):
        path = _write_yaml(textwrap.dedent("""
            resolvers:
              x:
                - kind: callable
                  name: api
                  priority: 0
                  fetch: undefined_func
        """).strip())

        with self.assertRaisesRegex(KeyError, "fetch.*undefined_func"):
            load_resolvers_from_yaml(
                path, allowed_categories=["x"], fetchers={},
            )

    def test_callable_missing_fetch_field_raises(self):
        path = _write_yaml(textwrap.dedent("""
            resolvers:
              x:
                - kind: callable
                  name: api
                  priority: 0
        """).strip())

        with self.assertRaisesRegex(ValueError, "needs 'fetch' field"):
            load_resolvers_from_yaml(path, allowed_categories=["x"])


# ═══════════════════════════════════════════════════════════════════
# yaml_match kind
# ═══════════════════════════════════════════════════════════════════

class YamlMatchKindTests(unittest.TestCase):
    def test_yaml_match_with_matcher(self):
        path = _write_yaml(textwrap.dedent("""
            resolvers:
              bom:
                - kind: yaml_match
                  name: market
                  priority: 100
                  data:
                    "鸡块（中）": [["MAT1", "鸡", 50, "g"]]
                  matcher: bom_matcher
        """).strip())

        def matcher(key, data):
            # 模糊匹配: 找包含 key 的 data key
            if key in data:
                return key
            for k in data:
                if key in k:
                    return k
            return None

        out = load_resolvers_from_yaml(
            path,
            allowed_categories=["bom"],
            matchers={"bom_matcher": matcher},
        )
        r = out["bom"]

        result = r.resolve("鸡块")
        self.assertIsNotNone(result)
        self.assertEqual(result.source, "market")

    def test_yaml_match_missing_matcher_raises(self):
        path = _write_yaml(textwrap.dedent("""
            resolvers:
              bom:
                - kind: yaml_match
                  name: market
                  priority: 0
                  data: {x: 1}
                  matcher: undefined
        """).strip())

        with self.assertRaisesRegex(KeyError, "matcher.*undefined"):
            load_resolvers_from_yaml(
                path, allowed_categories=["bom"], matchers={},
            )


# ═══════════════════════════════════════════════════════════════════
# 错误处理
# ═══════════════════════════════════════════════════════════════════

class ErrorHandlingTests(unittest.TestCase):
    def test_unknown_kind_raises(self):
        path = _write_yaml(textwrap.dedent("""
            resolvers:
              x:
                - kind: weird
                  name: a
                  priority: 0
        """).strip())

        with self.assertRaisesRegex(ValueError, "Unknown provider kind 'weird'"):
            load_resolvers_from_yaml(path, allowed_categories=["x"])

    def test_dict_kind_without_data_or_path_raises(self):
        path = _write_yaml(textwrap.dedent("""
            resolvers:
              x:
                - kind: dict
                  name: a
                  priority: 0
        """).strip())

        with self.assertRaisesRegex(ValueError, "must have 'data'"):
            load_resolvers_from_yaml(path, allowed_categories=["x"])

    def test_inline_data_must_be_dict(self):
        path = _write_yaml(textwrap.dedent("""
            resolvers:
              x:
                - kind: dict
                  name: a
                  priority: 0
                  data: [1, 2, 3]
        """).strip())

        with self.assertRaisesRegex(TypeError, "must be a dict"):
            load_resolvers_from_yaml(path, allowed_categories=["x"])


# ═══════════════════════════════════════════════════════════════════
# Integration: 多 category 同时加载 (典型用法)
# ═══════════════════════════════════════════════════════════════════

class IntegrationTests(unittest.TestCase):
    def test_realistic_multi_category_config(self):
        """模拟 P3.5 P&L 报表的 resolvers.yaml 实际用法.

        commission_rate / labor_cost / store_attribute 三个类别同时加载,
        每个有自己的 priority 栈. 报表脚本读 resolvers["commission_rate"]
        .resolve("grab") 拿值.
        """
        path = _write_yaml(textwrap.dedent("""
            resolvers:
              commission_rate:
                - kind: dict
                  name: defaults
                  priority: 0
                  data: {grab: 0.30, lineman: 0.25, shopee: 0.20}
                - kind: dict
                  name: hr_negotiated
                  priority: 100
                  data: {grab: 0.22}     # 大店谈到了优惠
              labor_cost:
                - kind: dict
                  name: monthly_hr_export
                  priority: 100
                  data: {shop2598648160256000: 150000.0}
              store_attribute:
                - kind: dict
                  name: company_dict
                  priority: 50
                  data: {shop2598648160256000: {type: "flagship", region: "bkk"}}
              # 未授权 category, 应该被白名单过滤
              evil_override:
                - kind: dict
                  name: x
                  priority: 999
                  data: {anything: "hacked"}
        """).strip())

        out = load_resolvers_from_yaml(
            path,
            allowed_categories=["commission_rate", "labor_cost", "store_attribute"],
        )

        # 三个授权 category 都加载
        self.assertEqual(set(out.keys()), {"commission_rate", "labor_cost", "store_attribute"})

        # evil_override 没在白名单, 不在 out 里
        self.assertNotIn("evil_override", out)

        # commission_rate priority 栈正确
        grab_r = out["commission_rate"].resolve("grab")
        self.assertEqual(grab_r.value, 0.22)  # hr_negotiated 100 > defaults 0
        self.assertEqual(grab_r.source, "hr_negotiated")

        lineman_r = out["commission_rate"].resolve("lineman")
        self.assertEqual(lineman_r.value, 0.25)
        self.assertEqual(lineman_r.source, "defaults")  # 只在 defaults 命中

        # labor_cost
        labor_r = out["labor_cost"].resolve("shop2598648160256000")
        self.assertEqual(labor_r.value, 150000.0)

        # store_attribute (value 是 dict, 返回原 dict)
        attr_r = out["store_attribute"].resolve("shop2598648160256000")
        self.assertEqual(attr_r.value, {"type": "flagship", "region": "bkk"})

        # 没存在的 store 返回 None
        self.assertIsNone(out["labor_cost"].resolve("nonexistent_shop"))


if __name__ == "__main__":
    unittest.main()
