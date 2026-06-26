"""P1 Parity tests — 新 Resolver-based 实现 ≡ 旧 priority 栈实现。

设计目的: 在改造 profit_margin_report.py 的 `_match_bom_layered` 和
`_resolve_unit_price_with_source` 时，需要"行为零变化"的强证据。

策略: 同一份 fixture 输入跑两套路径:
  - 旧: 直接调老函数 (_match_bom_layered / _resolve_unit_price_with_source)
  - 新: 用 _build_bom_resolver / _build_material_price_resolver + Resolver.resolve
断言输出完全一致 (value + source 字符串都要相等)。

边界 case 必须覆盖:
  - 大小写不敏感 (key / KEY / key)
  - 模糊 BOM 名匹配 5 层 (精确 / 中文段 / 起始 / 包含 / 长前缀)
  - 客户外挂多层 priority 顺序
  - 客户外挂层 dict / float value 两种格式
  - 客户外挂层 index_by code / name 双索引
  - strict 模式
  - 空输入 (None / "" / empty dict)
  - source 字符串完全匹配 (含 [source_tag] 后缀)

跑法: venv/bin/python -m unittest tests.test_resolver_parity -v
"""
from __future__ import annotations

import unittest

from semantic.resolvers.layered_resource import Layer
from bq_reports.profit_margin_report import (
    BOM_UNIT_CORRECTIONS,
    _build_bom_resolver,
    _build_material_price_resolver,
    _match_bom_layered,
    _match_fallback_bom,
    _resolve_unit_price_with_source,
)


# ═══════════════════════════════════════════════════════════════════
# Ground-truth fixtures — 旧 priority 栈实现的"冻结副本"
# 生产代码已切换到 Resolver-based 实现; ground-truth 留在测试里作为对照,
# 让 parity test 持续提供"行为零变化"的保护, 不退化为 self-test.
#
# 不要在生产代码里再调这些函数, 它们只用于测试. 改业务规则时一并改这里
# 跟生产代码的 _build_*_resolver, 让 parity 仍然成立.
# ═══════════════════════════════════════════════════════════════════

def _legacy_match_bom_layered(item_name, bom_layers):
    """旧 _match_bom_layered 实现 (P1 重构前) — 按列表顺序遍历."""
    if not item_name or not bom_layers:
        return None, None
    for name, _priority, boms in bom_layers:
        matched = _match_fallback_bom(item_name, boms)
        if matched:
            return matched, name
    return None, None


def _legacy_resolve_unit_price_with_source(
    material_code, bq_price, uploaded_prices, erp_prices,
    price_layers=None, strict=False, material_name=None,
):
    """旧 _resolve_unit_price_with_source 实现 (P1 重构前) — 嵌套 if-else 路径."""
    if price_layers:
        from utils.resource_adapter import _normalize_overseas_suffix
        for layer in price_layers:
            index_by = getattr(layer, 'index_by', 'code')
            if index_by == 'code':
                if not material_code:
                    continue
                for key in (material_code, str(material_code).upper(), str(material_code).lower()):
                    if key in layer.data:
                        v = layer.data[key]
                        if isinstance(v, dict):
                            return float(v['price']), f"{layer.name}[{v.get('source_tag','')}]"
                        return float(v), layer.name
            elif index_by == 'name':
                if not material_name:
                    continue
                nname = _normalize_overseas_suffix(material_name)
                if nname and nname in layer.data:
                    v = layer.data[nname]
                    if isinstance(v, dict):
                        return float(v['price']), f"{layer.name}[{v.get('source_tag','')}]"
                    return float(v), layer.name

    if strict:
        return 0.0, '无 (strict)'

    if not material_code:
        return float(bq_price or 0), 'bq_native'

    if uploaded_prices:
        for key in (material_code, material_code.upper(), material_code.lower()):
            if key in uploaded_prices:
                return uploaded_prices[key], 'uploaded_price_list'

    if erp_prices:
        for key in (material_code, material_code.upper(), material_code.lower()):
            if key in erp_prices:
                price, _uom = erp_prices[key]
                for corr_key in (material_code, material_code.upper(), material_code.lower()):
                    if corr_key in BOM_UNIT_CORRECTIONS:
                        price = price / BOM_UNIT_CORRECTIONS[corr_key]
                        break
                return price, 'ERPNext'

    return float(bq_price or 0), 'bq_native'


# ═══════════════════════════════════════════════════════════════════
# BOM matching parity
# ═══════════════════════════════════════════════════════════════════

def _new_match_bom(item_name, bom_layers):
    """用 Resolver 实现等价的 _match_bom_layered (= 生产 _match_bom_layered)."""
    if not item_name or not bom_layers:
        return None, None
    resolver = _build_bom_resolver(bom_layers)
    result = resolver.resolve(item_name)
    if result is None:
        return None, None
    return result.value, result.source


class BomMatchParity(unittest.TestCase):
    """新旧 BOM 匹配路径输出必须 byte-equal.

    左边: _legacy_match_bom_layered (P1 重构前实现, 冻结副本作为 ground truth)
    右边: _match_bom_layered (生产实现, 现在是 Resolver-based) === _new_match_bom
    """

    def _assert_parity(self, item_name, bom_layers, msg=""):
        old_v, old_s = _legacy_match_bom_layered(item_name, bom_layers)
        new_v, new_s = _match_bom_layered(item_name, bom_layers)
        self.assertEqual(old_v, new_v, msg=f"value mismatch [{msg}]")
        self.assertEqual(old_s, new_s, msg=f"source mismatch [{msg}]")

    # ─── 基本场景 ───

    def test_empty_inputs(self):
        self._assert_parity(None, [], "None item, empty layers")
        self._assert_parity("", [], "empty string item")
        self._assert_parity("鸡块", None, "None bom_layers")
        self._assert_parity("鸡块", [], "empty bom_layers")

    def test_no_match(self):
        layers = [("market", 100, {"汉堡": [("MAT1", "面包", 1, "pc")]})]
        self._assert_parity("不存在的商品", layers, "no match")

    def test_single_layer_exact(self):
        layers = [("market", 100, {"鸡块（中）": [("MAT1", "鸡肉", 50, "g")]})]
        self._assert_parity("鸡块（中）", layers, "exact key match")

    # ─── 模糊匹配 5 层 ───

    def test_fuzzy_chinese_section_exact(self):
        # key 是 "鸡块（中） / Chicken Nuggets (M)"; item="鸡块（中）"
        layers = [("market", 100, {
            "鸡块（中） / Chicken Nuggets (M)": [("MAT1", "鸡", 50, "g")],
        })]
        self._assert_parity("鸡块（中）", layers, "chinese section exact")

    def test_fuzzy_starts_with(self):
        # zh_section="鸡块（中）" startswith "鸡块"
        layers = [("market", 100, {
            "鸡块（中）": [("MAT1", "鸡", 50, "g")],
        })]
        self._assert_parity("鸡块", layers, "starts-with fuzzy")

    def test_fuzzy_contains(self):
        # zh_section="周一特惠 - 鸡肉芝士球 2 盒 69" 包含 "鸡肉芝士球"
        layers = [("market", 100, {
            "周一特惠 - 鸡肉芝士球 2 盒 69": [("MAT1", "芝士", 30, "g")],
        })]
        self._assert_parity("鸡肉芝士球", layers, "contains fuzzy")

    def test_fuzzy_prefers_shortest_match(self):
        # item="鸡块" 同时能 startswith "鸡块（中）" 和 "鸡块（大）（豪华版本）"
        # 应该选短的
        layers = [("market", 100, {
            "鸡块（大）（豪华版本套餐）": [("MAT_long", "鸡", 100, "g")],
            "鸡块（中）": [("MAT_short", "鸡", 50, "g")],
        })]
        self._assert_parity("鸡块", layers, "shortest match preferred")

    # ─── Priority 层叠 ───

    def test_multi_layer_priority(self):
        layers = [
            ("market", 100, {"鸡块": [("MAT_HIGH", "鸡(market)", 50, "g")]}),
            ("bq",     10,  {"鸡块": [("MAT_LOW",  "鸡(bq)",     30, "g")]}),
        ]
        old_v, old_s = _match_bom_layered("鸡块", layers)
        new_v, new_s = _new_match_bom("鸡块", layers)
        self.assertEqual(old_v, new_v)
        self.assertEqual(old_s, new_s)
        self.assertEqual(old_s, "market")  # 高 priority 赢

    def test_multi_layer_fallback(self):
        # high layer 没有 "鸡块"，should fallback to low layer
        layers = [
            ("market", 100, {"汉堡": [("MAT_A", "面包", 1, "pc")]}),
            ("bq",     10,  {"鸡块": [("MAT_B", "鸡",   50, "g")]}),
        ]
        old_v, old_s = _match_bom_layered("鸡块", layers)
        new_v, new_s = _new_match_bom("鸡块", layers)
        self.assertEqual(old_v, new_v)
        self.assertEqual(old_s, new_s)
        self.assertEqual(old_s, "bq")


# ═══════════════════════════════════════════════════════════════════
# Material price parity
# ═══════════════════════════════════════════════════════════════════

def _new_resolve_unit_price(material_code, bq_price, uploaded_prices, erp_prices,
                             price_layers=None, strict=False, material_name=None):
    """用 Resolver 实现等价的 _resolve_unit_price_with_source。

    旧函数有一些特殊路径不进 Resolver:
      - if not material_code and not strict: 直接 (bq_price, 'bq_native')
        但要注意 strict=False 且 price_layers index_by='name' 时仍可命中
      - strict: 客户外挂未命中即 (0, '无 (strict)')
      - 全栈未命中: (bq_price, 'bq_native')
    """
    resolver = _build_material_price_resolver(
        uploaded_prices, erp_prices, price_layers, strict=strict
    )
    result = resolver.resolve((material_code, material_name))

    if result is not None:
        return result.value, result.source

    # 全栈未命中
    if strict:
        return 0.0, "无 (strict)"

    return float(bq_price or 0), "bq_native"


class MaterialPriceParity(unittest.TestCase):
    """新旧物料单价路径输出必须 byte-equal.

    左边: _legacy_resolve_unit_price_with_source (P1 重构前实现, 冻结副本)
    右边: _resolve_unit_price_with_source (生产实现, 现在是 Resolver-based)
    """

    def _assert_parity(self, material_code, bq_price, uploaded_prices, erp_prices,
                       price_layers=None, strict=False, material_name=None, msg=""):
        old_p, old_s = _legacy_resolve_unit_price_with_source(
            material_code, bq_price, uploaded_prices, erp_prices,
            price_layers=price_layers, strict=strict, material_name=material_name,
        )
        new_p, new_s = _resolve_unit_price_with_source(
            material_code, bq_price, uploaded_prices, erp_prices,
            price_layers=price_layers, strict=strict, material_name=material_name,
        )
        self.assertEqual(old_p, new_p, msg=f"price mismatch [{msg}]")
        self.assertEqual(old_s, new_s, msg=f"source mismatch [{msg}]")

    # ─── 兜底场景 ───

    def test_no_layers_no_uploaded_no_erp(self):
        self._assert_parity("MAT1", 5.5, None, None, msg="empty stack → bq_native")

    def test_empty_dicts(self):
        self._assert_parity("MAT1", 5.5, {}, {}, msg="empty dicts → bq_native")

    def test_no_material_code_bq_fallback(self):
        self._assert_parity(None, 9.9, {"MAT1": 1.0}, None,
                           msg="None code → bq_native")
        self._assert_parity("", 9.9, {"MAT1": 1.0}, None,
                           msg="empty code → bq_native")

    # ─── 上传清单 ───

    def test_uploaded_price_hit(self):
        self._assert_parity("MAT1", 99.0, {"MAT1": 3.5}, None,
                           msg="uploaded hit")

    def test_uploaded_case_insensitive(self):
        self._assert_parity("mat1", 99.0, {"MAT1": 3.5}, None,
                           msg="uploaded upper-case key")
        self._assert_parity("MAT1", 99.0, {"mat1": 3.5}, None,
                           msg="uploaded lower-case key")

    def test_uploaded_miss_fallback_to_bq(self):
        self._assert_parity("MAT_X", 7.7, {"MAT1": 3.5}, None,
                           msg="uploaded miss → bq")

    # ─── ERPNext (含单位修正) ───

    def test_erpnext_hit(self):
        self._assert_parity("MAT1", 99.0, None, {"MAT1": (10.0, "kg")},
                           msg="ERPNext hit")

    def test_erpnext_unit_correction(self):
        # MK01018 在 BOM_UNIT_CORRECTIONS = {"MK01018": 50}
        # erp 报价 100, 应除以 50 = 2.0
        self._assert_parity("MK01018", 99.0, None, {"MK01018": (100.0, "g")},
                           msg="ERPNext with unit correction")

    def test_uploaded_beats_erpnext(self):
        # uploaded priority 80 > ERPNext priority 50
        self._assert_parity("MAT1", 99.0, {"MAT1": 3.5}, {"MAT1": (10.0, "kg")},
                           msg="uploaded beats ERPNext")

    # ─── 客户外挂层 (index_by=code) ───

    def test_customer_layer_by_code_float_value(self):
        layer = Layer(name="market_20260513", priority=200,
                      data={"MAT1": 1.5})
        layer.index_by = "code"
        self._assert_parity("MAT1", 99.0, None, None, price_layers=[layer],
                           msg="customer code float")

    def test_customer_layer_by_code_dict_value(self):
        """新格式 value 是 {price, source_tag, ...}; source 应带 [tag] 后缀."""
        layer = Layer(name="market", priority=200,
                      data={"MAT1": {"price": 2.5, "source_tag": "taixi_20260513"}})
        layer.index_by = "code"
        self._assert_parity("MAT1", 99.0, None, None, price_layers=[layer],
                           msg="customer code dict (new format)")

    def test_customer_layer_by_code_case_insensitive(self):
        layer = Layer(name="m", priority=100, data={"MAT1": 1.0})
        layer.index_by = "code"
        self._assert_parity("mat1", 99.0, None, None, price_layers=[layer],
                           msg="customer code case-insensitive")

    # ─── 客户外挂层 (index_by=name) ───

    def test_customer_layer_by_name(self):
        layer = Layer(name="import_20260501", priority=300,
                      data={"鸡肉碎": {"price": 5.5, "source_tag": "import"}})
        layer.index_by = "name"
        self._assert_parity("MAT1", 99.0, None, None, price_layers=[layer],
                           material_name="鸡肉碎",
                           msg="customer name dict")

    def test_customer_layer_by_name_missing_name_falls_through(self):
        """index_by=name 但 material_name 为空 → 该层跳过."""
        layer = Layer(name="market", priority=200,
                      data={"鸡肉碎": 5.5})
        layer.index_by = "name"
        self._assert_parity("MAT1", 99.0, {"MAT1": 3.5}, None,
                           price_layers=[layer], material_name=None,
                           msg="name layer skipped when name=None → uploaded hit")

    # ─── 多层 + 优先级 ───

    def test_customer_beats_uploaded_and_erpnext(self):
        layer = Layer(name="market", priority=300, data={"MAT1": 1.0})
        layer.index_by = "code"
        self._assert_parity("MAT1", 99.0, {"MAT1": 3.5}, {"MAT1": (10.0, "kg")},
                           price_layers=[layer],
                           msg="customer beats all")

    def test_high_priority_customer_beats_low(self):
        """生产真实场景: _load_material_price_layers 返回的 layers 已按 priority 降序排好.

        旧函数 _resolve_unit_price_with_source 按列表顺序遍历, 依赖输入预排序.
        在生产场景这等价于按 priority 降序 — parity 成立.
        """
        high = Layer(name="market_hi", priority=300, data={"MAT1": 1.0})
        high.index_by = "code"
        low = Layer(name="market_lo", priority=100, data={"MAT1": 9.9})
        low.index_by = "code"
        # 按 priority 降序传入 (生产场景)
        self._assert_parity("MAT1", 99.0, None, None,
                           price_layers=[high, low],
                           msg="high priority wins (sorted input, prod scenario)")

    # ─── Strict 模式 ───

    def test_strict_customer_hit(self):
        layer = Layer(name="market", priority=100, data={"MAT1": 1.0})
        layer.index_by = "code"
        self._assert_parity("MAT1", 99.0, {"MAT1": 3.5}, {"MAT1": (10.0, "kg")},
                           price_layers=[layer], strict=True,
                           msg="strict + customer hit")

    def test_strict_customer_miss_returns_zero(self):
        """strict 模式 customer 未命中, 不 fallback → (0, '无 (strict)')."""
        layer = Layer(name="market", priority=100, data={"MAT_X": 1.0})
        layer.index_by = "code"
        self._assert_parity("MAT1", 99.0, {"MAT1": 3.5}, {"MAT1": (10.0, "kg")},
                           price_layers=[layer], strict=True,
                           msg="strict miss → 0")

    def test_strict_no_layers_returns_zero(self):
        self._assert_parity("MAT1", 99.0, {"MAT1": 3.5}, {"MAT1": (10.0, "kg")},
                           price_layers=None, strict=True,
                           msg="strict + no layers → 0")


# ═══════════════════════════════════════════════════════════════════
# Bonus robustness tests — 新 Resolver 比旧函数更健壮的部分
# 不要求 parity, 只 assert 新实现的改进点
# ═══════════════════════════════════════════════════════════════════

class ResolverBonusRobustness(unittest.TestCase):
    def test_resolver_auto_sorts_unsorted_input(self):
        """新 Resolver 自动按 priority 重排, 不依赖输入预排序.

        旧 _resolve_unit_price_with_source 用 `for layer in price_layers` 按列表
        顺序遍历, 输入乱序会出错. 新 Resolver 构造时主动 sort, 健壮性提升.
        这是 P1 重构的 bonus.
        """
        high = Layer(name="market_hi", priority=300, data={"MAT1": 1.0})
        high.index_by = "code"
        low = Layer(name="market_lo", priority=100, data={"MAT1": 9.9})
        low.index_by = "code"

        # 故意以低优先级在前的顺序构造 Resolver
        resolver = _build_material_price_resolver(None, None, [low, high])
        result = resolver.resolve(("MAT1", None))

        # 新实现总是返回高 priority 的值
        self.assertEqual(result.value, 1.0)
        self.assertEqual(result.source, "market_hi")
        self.assertEqual(result.priority, 300)

    def test_bom_resolver_auto_sorts(self):
        """BOM Resolver 同样自动重排."""
        layers = [
            ("low",  10,  {"鸡块": [("MAT_LOW", "鸡", 30, "g")]}),
            ("high", 100, {"鸡块": [("MAT_HIGH", "鸡", 50, "g")]}),
        ]
        resolver = _build_bom_resolver(layers)
        result = resolver.resolve("鸡块")
        self.assertEqual(result.value, [("MAT_HIGH", "鸡", 50, "g")])
        self.assertEqual(result.source, "high")


if __name__ == "__main__":
    unittest.main()
