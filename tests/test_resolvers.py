"""Tests for semantic/resolvers/ — Provider Protocol + Resolver + Builder.

跑法: venv/bin/python -m unittest tests.test_resolvers -v
"""
from __future__ import annotations

import unittest

from semantic.resolvers import (
    CallableProvider,
    DictProvider,
    Provider,
    Resolved,
    Resolver,
    YamlMatchProvider,
    build_resolver,
    from_layers,
    from_layers_with_matcher,
)


# ═══════════════════════════════════════════════════════════════════
# DictProvider
# ═══════════════════════════════════════════════════════════════════

class DictProviderTests(unittest.TestCase):
    def test_basic_lookup(self):
        p = DictProvider(name="src1", priority=10, data={"A": 1, "B": 2})
        self.assertEqual(p.get("A"), 1)
        self.assertEqual(p.get("B"), 2)
        self.assertIsNone(p.get("C"))
        self.assertEqual(p.name, "src1")
        self.assertEqual(p.priority, 10)

    def test_satisfies_protocol(self):
        """duck typing: DictProvider 是 Provider Protocol 的合法实现。"""
        p = DictProvider(name="x", priority=0, data={})
        self.assertIsInstance(p, Provider)

    def test_frozen(self):
        """frozen=True: Provider 创建后不可变。"""
        p = DictProvider(name="x", priority=0, data={})
        with self.assertRaises(Exception):  # FrozenInstanceError
            p.name = "y"  # noqa


# ═══════════════════════════════════════════════════════════════════
# Resolver — priority & resolve
# ═══════════════════════════════════════════════════════════════════

class ResolverPriorityTests(unittest.TestCase):
    def test_priority_descending(self):
        high = DictProvider(name="high", priority=100, data={"A": 99})
        low = DictProvider(name="low", priority=50, data={"A": 1, "B": 2})

        # 故意以低优先级在前的顺序传入，验证 Resolver 自己重排
        r = Resolver([low, high])

        rA = r.resolve("A")
        self.assertIsInstance(rA, Resolved)
        self.assertEqual(rA.value, 99)
        self.assertEqual(rA.source, "high")
        self.assertEqual(rA.priority, 100)

        rB = r.resolve("B")
        self.assertEqual(rB.value, 2)
        self.assertEqual(rB.source, "low")
        self.assertEqual(rB.priority, 50)

        self.assertIsNone(r.resolve("Z"))

    def test_same_priority_stable_order(self):
        """同 priority 时按插入顺序稳定。"""
        a = DictProvider(name="a", priority=50, data={"K": 1})
        b = DictProvider(name="b", priority=50, data={"K": 2})

        r = Resolver([a, b])
        self.assertEqual(r.resolve("K").source, "a")  # 先插入的赢

        r2 = Resolver([b, a])
        self.assertEqual(r2.resolve("K").source, "b")

    def test_resolve_or_default_hits(self):
        p = DictProvider(name="p", priority=0, data={"X": 10})
        r = Resolver([p])

        rX = r.resolve_or_default("X", default=999)
        self.assertEqual(rX.value, 10)
        self.assertEqual(rX.source, "p")
        self.assertEqual(rX.priority, 0)

    def test_resolve_or_default_falls_back(self):
        p = DictProvider(name="p", priority=0, data={"X": 10})
        r = Resolver([p])

        rY = r.resolve_or_default("Y", default=999)
        self.assertEqual(rY.value, 999)
        self.assertEqual(rY.source, "default")
        self.assertEqual(rY.priority, -1)  # 合成 Resolved 标记

    def test_resolve_or_default_custom_source(self):
        r = Resolver([])
        out = r.resolve_or_default("X", default=0, default_source="fallback_zero")
        self.assertEqual(out.value, 0)
        self.assertEqual(out.source, "fallback_zero")

    def test_empty_resolver(self):
        r = Resolver([])
        self.assertIsNone(r.resolve("anything"))
        self.assertEqual(len(r), 0)
        self.assertFalse(bool(r))

    def test_repr(self):
        r = Resolver([
            DictProvider(name="a", priority=10, data={}),
            DictProvider(name="b", priority=20, data={}),
        ], name="bom")
        s = repr(r)
        self.assertIn("bom", s)
        self.assertIn("b(p=20)", s)
        self.assertIn("a(p=10)", s)
        # 高优先级在前
        self.assertLess(s.index("b(p=20)"), s.index("a(p=10)"))

    def test_providers_view_is_a_copy(self):
        p1 = DictProvider(name="a", priority=10, data={})
        p2 = DictProvider(name="b", priority=20, data={})
        r = Resolver([p1, p2])

        view = r.providers
        self.assertEqual(view, [p2, p1])  # 按 priority 降序
        # 改 view 不影响内部
        view.clear()
        self.assertEqual(len(r), 2)


# ═══════════════════════════════════════════════════════════════════
# YamlMatchProvider — fuzzy match
# ═══════════════════════════════════════════════════════════════════

class YamlMatchProviderTests(unittest.TestCase):
    def test_with_simple_matcher(self):
        data = {"鸡块（中）": "MAT1", "番茄薯条": "MAT2"}

        def matcher(key, d):
            if key in d:
                return key
            for k in d:
                if key in k:
                    return k
            return None

        p = YamlMatchProvider(name="market", priority=100, data=data, matcher=matcher)
        self.assertEqual(p.get("鸡块"), "MAT1")
        self.assertEqual(p.get("鸡块（中）"), "MAT1")  # 精确也命中
        self.assertEqual(p.get("薯条"), "MAT2")
        self.assertIsNone(p.get("汉堡"))

    def test_matcher_returning_none(self):
        """matcher 返回 None 时 get() 返回 None，不抛 KeyError。"""
        def matcher(k, d):
            return None
        p = YamlMatchProvider(name="x", priority=0, data={"a": 1}, matcher=matcher)
        self.assertIsNone(p.get("anything"))


# ═══════════════════════════════════════════════════════════════════
# CallableProvider — lazy fetch
# ═══════════════════════════════════════════════════════════════════

class CallableProviderTests(unittest.TestCase):
    def test_records_calls(self):
        call_log = []

        def fetch(k):
            call_log.append(k)
            return {"A": 1, "B": 2}.get(k)

        p = CallableProvider(name="erpnext", priority=50, fetch=fetch)
        self.assertEqual(p.get("A"), 1)
        self.assertIsNone(p.get("Z"))
        self.assertEqual(call_log, ["A", "Z"])

    def test_resolver_stops_at_first_hit(self):
        """前面命中后不会再调后面的 callable，省 API/查询开销。"""
        high_calls = []
        low_calls = []

        def high_fetch(k):
            high_calls.append(k)
            return {"A": 99}.get(k)

        def low_fetch(k):
            low_calls.append(k)
            return {"A": 1, "B": 2}.get(k)

        high = CallableProvider(name="high", priority=100, fetch=high_fetch)
        low = CallableProvider(name="low", priority=10, fetch=low_fetch)
        r = Resolver([high, low])

        self.assertEqual(r.resolve("A").value, 99)
        self.assertEqual(high_calls, ["A"])
        self.assertEqual(low_calls, [])  # high 命中，low 没被调

        self.assertEqual(r.resolve("B").value, 2)
        self.assertEqual(high_calls, ["A", "B"])  # high 试了
        self.assertEqual(low_calls, ["B"])        # low 才被调


# ═══════════════════════════════════════════════════════════════════
# Builder — yaml-style config
# ═══════════════════════════════════════════════════════════════════

class BuilderTests(unittest.TestCase):
    def test_dict_kind(self):
        r = build_resolver("test", [
            {"kind": "dict", "name": "high", "priority": 100, "data": {"A": 99}},
            {"kind": "dict", "name": "low",  "priority": 50,  "data": {"A": 1}},
        ])
        rA = r.resolve("A")
        self.assertEqual(rA.value, 99)
        self.assertEqual(rA.source, "high")

    def test_callable_via_context_string_ref(self):
        fetched = []

        def my_fetch(k):
            fetched.append(k)
            return f"val_{k}"

        r = build_resolver("test", [
            {"kind": "callable", "name": "api", "priority": 10, "fetch": "my_fetch"},
        ], context={"my_fetch": my_fetch})

        rA = r.resolve("X")
        self.assertEqual(rA.value, "val_X")
        self.assertEqual(rA.source, "api")
        self.assertEqual(fetched, ["X"])

    def test_callable_direct_callable(self):
        """yaml 之外的调用入口允许直接传 callable。"""
        r = build_resolver("test", [
            {"kind": "callable", "name": "api", "priority": 10,
             "fetch": lambda k: f"val_{k}"},
        ])
        self.assertEqual(r.resolve("X").value, "val_X")

    def test_yaml_match_kind(self):
        def matcher(k, d):
            return k if k in d else None

        r = build_resolver("test", [
            {"kind": "yaml_match", "name": "src", "priority": 0,
             "data": {"A": 1}, "matcher": "m1"},
        ], context={"m1": matcher})

        self.assertEqual(r.resolve("A").value, 1)

    def test_unknown_kind_raises(self):
        with self.assertRaisesRegex(ValueError, "Unknown provider kind"):
            build_resolver("test", [
                {"kind": "weird", "name": "x", "priority": 0},
            ])

    def test_missing_context_ref_raises(self):
        with self.assertRaisesRegex(KeyError, "not in context"):
            build_resolver("test", [
                {"kind": "callable", "name": "api", "priority": 0,
                 "fetch": "nonexistent_func"},
            ], context={})


# ═══════════════════════════════════════════════════════════════════
# Legacy bridge — from_layers / from_layers_with_matcher
# ═══════════════════════════════════════════════════════════════════

class LegacyBridgeTests(unittest.TestCase):
    def test_from_layers(self):
        from semantic.resolvers.layered_resource import Layer
        layers = [
            Layer(name="A", priority=100, data={"x": 1}),
            Layer(name="B", priority=50, data={"x": 0, "y": 2}),
        ]
        r = from_layers("test", layers)

        rx = r.resolve("x")
        self.assertEqual(rx.value, 1)
        self.assertEqual(rx.source, "A")

        ry = r.resolve("y")
        self.assertEqual(ry.value, 2)
        self.assertEqual(ry.source, "B")

        self.assertIsNone(r.resolve("z"))

    def test_from_layers_with_matcher(self):
        """模拟 BOM 模糊匹配场景，验证 Resolver 行为跟 lookup_layered 等价。"""
        from semantic.resolvers.layered_resource import Layer

        layers = [
            Layer(name="market", priority=100, data={"鸡块（中）": [1, 2, 3]}),
            Layer(name="bq",     priority=10,  data={"鸡块":       [9]}),
        ]

        def matcher(key, d):
            if key in d:
                return key
            for k in d:
                if key in k:
                    return k
            return None

        r = from_layers_with_matcher("bom", layers, matcher)

        # "鸡块" 在 market 层模糊匹配到 "鸡块（中）"; 高 priority 赢
        rA = r.resolve("鸡块")
        self.assertEqual(rA.value, [1, 2, 3])
        self.assertEqual(rA.source, "market")

        # 不在任何层
        self.assertIsNone(r.resolve("汉堡"))


# ═══════════════════════════════════════════════════════════════════
# Resolved dataclass
# ═══════════════════════════════════════════════════════════════════

class ResolvedTests(unittest.TestCase):
    def test_immutable(self):
        r = Resolved(value=42, source="x", priority=10)
        with self.assertRaises(Exception):
            r.value = 100  # noqa

    def test_equality(self):
        a = Resolved(value=1, source="s", priority=0)
        b = Resolved(value=1, source="s", priority=0)
        c = Resolved(value=2, source="s", priority=0)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


# ═══════════════════════════════════════════════════════════════════
# Dynamic source — Provider.get() 返回 Resolved 对象
# ═══════════════════════════════════════════════════════════════════

class DynamicSourceProvider:
    """自定义 Provider，命中时返回带 source_tag 的 Resolved。

    用于模拟 profit_margin 物料价的"同一层内部条目带不同来源标签"场景:
    layer 名字 "market"，内部条目可能是 taixi 版或 import 版，需要把 tag
    拼到最终 source 里。
    """

    def __init__(self, name, priority, data, tag_lookup):
        self.name = name
        self.priority = priority
        self.data = data
        self.tag_lookup = tag_lookup  # {key: tag}

    def get(self, key):
        if key not in self.data:
            return None
        v = self.data[key]
        tag = self.tag_lookup.get(key, "")
        source = f"{self.name}[{tag}]" if tag else self.name
        return Resolved(value=v, source=source, priority=self.priority)


class DynamicSourceTests(unittest.TestCase):
    def test_provider_returns_resolved_directly(self):
        p = DynamicSourceProvider(
            name="market", priority=100,
            data={"MAT1": 5.0, "MAT2": 8.0},
            tag_lookup={"MAT1": "taixi_20260513", "MAT2": "import_20260501"},
        )
        r = Resolver([p])

        r1 = r.resolve("MAT1")
        self.assertEqual(r1.value, 5.0)
        self.assertEqual(r1.source, "market[taixi_20260513]")  # 动态 source
        self.assertEqual(r1.priority, 100)

        r2 = r.resolve("MAT2")
        self.assertEqual(r2.source, "market[import_20260501]")

    def test_mixed_dynamic_and_plain_providers(self):
        """同一 Resolver 里 dynamic + plain Provider 混合。"""
        dyn = DynamicSourceProvider(
            name="market", priority=100,
            data={"A": 99},
            tag_lookup={"A": "taixi"},
        )
        plain = DictProvider(name="bq_native", priority=10, data={"A": 1, "B": 2})
        r = Resolver([dyn, plain])

        # A 在 market 命中，带 dynamic source
        rA = r.resolve("A")
        self.assertEqual(rA.value, 99)
        self.assertEqual(rA.source, "market[taixi]")

        # B 在 bq_native 命中，用 provider.name 当 source
        rB = r.resolve("B")
        self.assertEqual(rB.value, 2)
        self.assertEqual(rB.source, "bq_native")

    def test_dynamic_provider_returns_none_falls_through(self):
        """dynamic Provider 返回 None 时按正常逻辑走到下一层。"""
        dyn = DynamicSourceProvider(
            name="market", priority=100,
            data={"A": 99}, tag_lookup={"A": "tag"},
        )
        fallback = DictProvider(name="fb", priority=10, data={"B": 222})
        r = Resolver([dyn, fallback])

        rB = r.resolve("B")  # market 没有 B, fallback 命中
        self.assertEqual(rB.value, 222)
        self.assertEqual(rB.source, "fb")


if __name__ == "__main__":
    unittest.main()
