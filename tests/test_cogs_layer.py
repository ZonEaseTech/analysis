"""smoke tests for semantic.cogs — 抽出层的 import 健康 + 关键纯函数行为.

跟 tests/test_price_resolver.py / test_resolver_parity.py / test_fallback_bom_match.py
有重叠覆盖 (那边走 bq_reports.profit_margin_report 私有别名), 这里走
semantic.cogs.* public API 直接打路径, 确保 re-export 链没断.
"""
from __future__ import annotations

import unittest

from semantic.cogs import (
    BOM_UNIT_CORRECTIONS,
    build_bom_resolver,
    build_material_price_resolver,
    exact_match_bom_key,
    expand_item_bom,
    find_matched_bom_key,
    match_bom_layered,
    match_fallback_bom,
    resolve_unit_price,
)


class CogsPublicApi(unittest.TestCase):

    def test_imports_present(self):
        for fn in (
            build_bom_resolver, build_material_price_resolver, expand_item_bom,
            find_matched_bom_key, match_bom_layered, match_fallback_bom,
            resolve_unit_price,
        ):
            self.assertTrue(callable(fn))
        # BOM_UNIT_CORRECTIONS 过渡期 legacy 修正仍在用 (待 desired-UOM 接进生产后退役)
        self.assertIn("MK01018", BOM_UNIT_CORRECTIONS)


class BomMatch(unittest.TestCase):

    def test_find_matched_key_exact(self):
        self.assertEqual(
            find_matched_bom_key("鸡块", {"鸡块": ["x"], "鸡腿": ["y"]}),
            "鸡块",
        )

    def test_find_matched_key_prefers_shortest_zh_segment(self):
        # 鸡肉芝士球 应优先匹配 短中文段, 而不是包含它的长名
        boms = {
            "鸡肉芝士球 / Chicken Cheese Ball": ["short"],
            "周一特惠 - 鸡肉芝士球 2 盒 69 / Mon Deal": ["long"],
        }
        self.assertEqual(
            find_matched_bom_key("鸡肉芝士球", boms),
            "鸡肉芝士球 / Chicken Cheese Ball",
        )

    def test_match_fallback_bom_returns_value(self):
        self.assertEqual(
            match_fallback_bom("鸡块", {"鸡块": ["recipe"]}),
            ["recipe"],
        )
        self.assertIsNone(match_fallback_bom("不存在", {"鸡块": ["recipe"]}))

    def test_match_bom_layered_priority(self):
        # 高 priority 命中, 不会落到低 priority
        high = ("client_v2", 200, {"鸡块": ["from-v2"]})
        low = ("erp_default", 100, {"鸡块": ["from-erp"]})
        matched, layer = match_bom_layered("鸡块", [low, high])
        self.assertEqual(matched, ["from-v2"])
        self.assertEqual(layer, "client_v2")

    def test_match_bom_layered_returns_none_on_miss(self):
        layers = [("only", 100, {"X": ["a"]})]
        self.assertEqual(match_bom_layered("Y", layers), (None, None))
        self.assertEqual(match_bom_layered("", layers), (None, None))
        self.assertEqual(match_bom_layered("X", []), (None, None))


class MaterialPrice(unittest.TestCase):

    def test_resolve_unit_price_uploaded_wins_over_erp(self):
        # uploaded (80) > ERPNext (50)
        price, src = resolve_unit_price(
            "M001", 99.0,
            uploaded_prices={"M001": 10.0},
            erp_prices={"M001": (20.0, "kg")},
        )
        self.assertEqual(price, 10.0)
        self.assertEqual(src, "uploaded_price_list")

    def test_resolve_unit_price_falls_back_to_bq_native(self):
        price, src = resolve_unit_price(
            "ZZZ", 5.5, uploaded_prices={}, erp_prices={},
        )
        self.assertEqual(price, 5.5)
        self.assertEqual(src, "bq_native")

    def test_resolve_unit_price_strict_returns_zero_on_miss(self):
        price, src = resolve_unit_price(
            "ZZZ", 5.5, uploaded_prices={}, erp_prices={}, strict=True,
        )
        self.assertEqual(price, 0.0)
        self.assertEqual(src, "无 (strict)")

    def test_erp_unit_correction_applied(self):
        # 过渡期 legacy 修正: MK01018 在 BOM_UNIT_CORRECTIONS = {"MK01018": 50}。
        # 不传 desired_uoms → 仅走 legacy 修正 → ERP 价 / 50。
        price, src = resolve_unit_price(
            "MK01018", 0,
            uploaded_prices={},
            erp_prices={"MK01018": (250.0, "kg")},
        )
        self.assertEqual(price, 5.0)   # 250 / 50
        self.assertEqual(src, "ERPNext")

    def test_erp_layer_rejects_uom_mismatch(self):
        # ERP 行单位 'ctn', BOM 消耗单位 'g' → 不得静默用, 应返回 None (缺口)
        erp = {"X": (300.0, "ctn")}
        r = build_material_price_resolver({}, erp, [], desired_uoms={"X": "g"})
        self.assertIsNone(r.resolve(("X", None)))  # UOM 不匹配 → 不命中, 交由上层判缺口

    def test_erp_layer_accepts_uom_match(self):
        # ERP 行单位 'g' == desired 'g' → 命中
        erp = {"X": (0.3, "g")}
        r = build_material_price_resolver({}, erp, [], desired_uoms={"X": "g"})
        res = r.resolve(("X", None))
        self.assertIsNotNone(res)
        self.assertAlmostEqual(res.value, 0.3)

    def test_erp_uom_match_still_applies_legacy_correction(self):
        # 共存: MK01018 传 desired_uoms 且 UOM 匹配 (ERP 也是 'pack') → UOM 校验
        # 通过后仍走 legacy ÷50, 证明新机制与过渡期修正共存不打架。
        erp = {"MK01018": (250.0, "pack")}
        r = build_material_price_resolver(
            {}, erp, [], desired_uoms={"MK01018": "pack"})
        res = r.resolve(("MK01018", None))
        self.assertIsNotNone(res)
        self.assertAlmostEqual(res.value, 5.0)  # 250 / 50, UOM 匹配后仍修正

    def test_erp_uom_mismatch_short_circuits_before_correction(self):
        # 共存边界: MK01018 desired='g' 而 ERP 是 'pack' → UOM 不匹配先判缺口,
        # legacy ÷50 不应执行 (返回 None, 不是 5.0)。
        erp = {"MK01018": (250.0, "pack")}
        r = build_material_price_resolver(
            {}, erp, [], desired_uoms={"MK01018": "g"})
        self.assertIsNone(r.resolve(("MK01018", None)))


class ExpandItemBom(unittest.TestCase):

    def test_single_dedup(self):
        # 同 material_code 多行 → 只保留一条 (首条)
        store_boms = {
            "item-A": [
                ("M001", "鸡肉", 0.1, "kg", 1.0, 0),
                ("M001", "鸡肉", 0.2, "kg", 1.0, 0),  # 应被 dedup 掉
                ("M002", "盐", 0.005, "kg", 1.0, 0),
            ],
        }
        result = expand_item_bom(
            "item-A", "single", store_boms, {},
            price_resolver=lambda c, n: 10.0,
        )
        self.assertEqual(set(result.keys()), {"M001", "M002"})
        self.assertEqual(result["M001"][1], 0.1)  # 首条的 num, 没累加

    def test_combo_weighted_sum(self):
        # 套餐 = 子A x1 weight=1 + 子B x2 weight=0.5
        # M001 在 A 出 0.1 kg → 0.1 * 1 * 1 = 0.1
        # M001 在 B 出 0.2 kg → 0.2 * 2 * 0.5 = 0.2
        # 合计 0.3 kg
        store_boms = {
            "child-A": [("M001", "鸡肉", 0.1, "kg", 1.0, 0)],
            "child-B": [("M001", "鸡肉", 0.2, "kg", 1.0, 0)],
        }
        store_struct = {
            "combo-X": [("child-A", 1, 1.0), ("child-B", 2, 0.5)],
        }
        result = expand_item_bom(
            "combo-X", "combo", store_boms, store_struct,
            price_resolver=lambda c, n: 10.0,
        )
        self.assertAlmostEqual(result["M001"][1], 0.3)
        self.assertEqual(result["M001"][2], 10.0)   # unit_price = 10 * conv 1

    def test_combo_legacy_str_spec(self):
        # 老 shape: spec 是 child_uuid 字符串 (不是 tuple), 视为 num=1 weight=1
        store_boms = {
            "child-A": [("M001", "x", 0.5, "kg", 1.0, 0)],
        }
        store_struct = {"combo-X": ["child-A"]}
        result = expand_item_bom(
            "combo-X", "combo", store_boms, store_struct,
            price_resolver=lambda c, n: 1.0,
        )
        self.assertAlmostEqual(result["M001"][1], 0.5)

    def test_empty_material_code_skipped(self):
        store_boms = {
            "item-A": [
                ("", "无 code 行", 0.1, "kg", 1.0, 0),
                ("M001", "鸡肉", 0.1, "kg", 1.0, 0),
            ],
        }
        result = expand_item_bom(
            "item-A", "single", store_boms, {},
            price_resolver=lambda c, n: 1.0,
        )
        self.assertEqual(list(result.keys()), ["M001"])


class ResolverBuilder(unittest.TestCase):

    def test_build_bom_resolver_returns_value_and_source(self):
        layers = [
            ("v2", 200, {"鸡块": ["a"]}),
            ("erp", 100, {"鸡块": ["b"]}),
        ]
        resolver = build_bom_resolver(layers)
        r = resolver.resolve("鸡块")
        self.assertEqual(r.value, ["a"])
        self.assertEqual(r.source, "v2")

    def test_build_material_price_resolver_strict_skips_uploaded(self):
        resolver = build_material_price_resolver(
            uploaded_prices={"M001": 99.0},
            erp_prices={"M001": (88.0, "kg")},
            price_layers=[],
            strict=True,
        )
        self.assertIsNone(resolver.resolve(("M001", None)))


class ExactMatchMode(unittest.TestCase):
    """match_mode: exact — 防"鸡块"误命中"鸡块（电影票折扣）"回归.

    背景: 补充 BOM 的 key 是市场从报表 copy 的精确商品名. 5 层模糊匹配会让
    短名"鸡块"前缀命中长 key"鸡块（电影票折扣）"(物料是薯条+鸡柳, 完全不同).
    commit 5de423e 用 match_mode: exact 修复.
    """

    PROMO_BOMS = {
        "鸡块（电影票折扣）": ["薯条+鸡柳"],
        "爆浆玉米 买一送一！": ["玉米"],
    }

    def test_exact_short_name_does_not_hit_long_key(self):
        # 普通"鸡块" 在 exact 模式下 不命中 "鸡块（电影票折扣）"
        self.assertIsNone(exact_match_bom_key("鸡块", self.PROMO_BOMS))

    def test_exact_full_name_hits(self):
        # 精确商品名 命中自己
        self.assertEqual(
            exact_match_bom_key("鸡块（电影票折扣）", self.PROMO_BOMS),
            "鸡块（电影票折扣）",
        )

    def test_exact_strips_whitespace(self):
        self.assertEqual(
            exact_match_bom_key("  鸡块（电影票折扣）  ", self.PROMO_BOMS),
            "鸡块（电影票折扣）",
        )

    def test_exact_edge_cases(self):
        self.assertIsNone(exact_match_bom_key("", self.PROMO_BOMS))
        self.assertIsNone(exact_match_bom_key("鸡块（电影票折扣）", {}))
        self.assertIsNone(exact_match_bom_key(None, self.PROMO_BOMS))

    def test_fuzzy_would_have_mismatched(self):
        # 对照: 同样数据走 fuzzy, "鸡块" 会误命中 — 证明 exact 是必要的
        hit = find_matched_bom_key("鸡块", self.PROMO_BOMS)
        self.assertEqual(hit, "鸡块（电影票折扣）")  # fuzzy 的确会误匹

    def test_build_bom_resolver_4tuple_exact_layer(self):
        # 4 元组 layer (name, priority, boms, match_mode) — exact 层 + fuzzy 层混栈
        layers = [
            ("补充BOM", 200, self.PROMO_BOMS, "exact"),
            ("核对版", 100, {"鸡块（标准）": ["真鸡块"]}, "fuzzy"),
        ]
        resolver = build_bom_resolver(layers)
        # "鸡块" exact 层不命中 → 落到 fuzzy 层命中"鸡块（标准）"
        r = resolver.resolve("鸡块")
        self.assertEqual(r.source, "核对版")
        self.assertEqual(r.value, ["真鸡块"])
        # "鸡块（电影票折扣）" exact 层精确命中
        r2 = resolver.resolve("鸡块（电影票折扣）")
        self.assertEqual(r2.source, "补充BOM")

    def test_build_bom_resolver_3tuple_still_fuzzy(self):
        # 向后兼容: 3 元组 layer 默认 fuzzy, 老 tests 不破
        layers = [("layer", 100, {"鸡块（中）": ["x"]})]
        resolver = build_bom_resolver(layers)
        self.assertEqual(resolver.resolve("鸡块").source, "layer")  # fuzzy 命中


if __name__ == "__main__":
    unittest.main()
