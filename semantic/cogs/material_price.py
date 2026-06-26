"""物料编码 / 名字 → 单价的多层 priority 解析.

来源: bq_reports/profit_margin_report.py 私有函数 (_MaterialPriceLayerProvider /
_build_material_price_resolver / _resolve_unit_price_with_source), 抽出为
semantic 层 public API。

单价 4 层 priority 栈 (高 → 低):
  1. price_layers     — 客户外挂物料价 (priority 来自 layer.priority, 100+)
                        支持 layer.index_by ∈ {'code', 'name'}
  2. uploaded_prices  — --price-list 上传清单 (priority 80, 按编码)
  3. erp_prices       — ERPNext Item Price (priority 50, 按 desired-UOM 校验)
  4. bq_price         — BQ ttpos_material 内置 (per-row, 不进 Resolver,
                        由 resolve_unit_price 兜底)

strict=True 时只走 price_layers, 全栈未命中 → (0, '无 (strict)') 不再 fallback。
"""
from __future__ import annotations

from semantic.resolvers import CallableProvider, Resolved, Resolver


# 过渡期 legacy 单位修正: MK01018 的 ERP Item Price 以 pack-of-50 录入, 而 BOM
# 按单件消耗, 故 ERP 价需 ÷50 才对齐 BOM 消耗单位。Key = material_code
# (大小写不敏感), Value = 除数。
# ⚠️ 这是过渡期 hack, 不是永久方案。正确做法是 ERP 在物料消耗 UOM 上维护
#    Item Price, 由 desired-UOM 校验 (本文件 _fetch_erp) 触发缺口报警替代此修正。
#    待 desired-UOM 校验接进生产路径 (Phase 5 / Task #6) 且核实 MK01018 的
#    消耗-UOM Item Price 后退役 (见 Task 4.1 anchor)。
BOM_UNIT_CORRECTIONS = {
    "MK01018": 50,
}


class MaterialPriceLayerProvider:
    """物料价"客户外挂"单层 Provider — 处理 index_by + value-format 双复杂度.

    一层物料价的复杂性:
      - layer.index_by ∈ {'code', 'name'}: 决定 key
      - value 可能是 float (老格式) 或 {price, source_tag, ...} dict (新格式)
      - 命中时 source 可能是 layer.name 或 f"{layer.name}[{source_tag}]"

    Provider.get() 返回 Resolved (自带 dynamic source), Resolver 直接转发。

    输入 key = (material_code, material_name) tuple — Resolver 内统一 key 形态。
    """

    def __init__(self, name, priority, data, index_by):
        self.name = name
        self.priority = priority
        self.data = data
        self.index_by = index_by

    def get(self, key):
        material_code, material_name = key

        if self.index_by == "code":
            if not material_code:
                return None
            for k in (
                material_code,
                str(material_code).upper(),
                str(material_code).lower(),
            ):
                if k in self.data:
                    return self._wrap(self.data[k])
        elif self.index_by == "name":
            if not material_name:
                return None
            from utils.resource_adapter import _normalize_overseas_suffix
            nname = _normalize_overseas_suffix(material_name)
            if nname and nname in self.data:
                return self._wrap(self.data[nname])
        return None

    def _wrap(self, v):
        """value → Resolved with proper source.

        新格式 ({price, source_tag, ...}): source = f"{layer.name}[{source_tag}]"
        老格式 (plain float): source = layer.name
        """
        if isinstance(v, dict):
            return Resolved(
                value=float(v["price"]),
                source=f"{self.name}[{v.get('source_tag', '')}]",
                priority=self.priority,
            )
        return Resolved(
            value=float(v), source=self.name, priority=self.priority
        )


def build_material_price_resolver(
    uploaded_prices,
    erp_prices,
    price_layers,
    *,
    strict=False,
    unit_corrections=None,
    desired_uoms=None,
):
    """构造物料单价 Resolver.

    Args:
        uploaded_prices: dict {material_code: float} 或 None
        erp_prices: dict {material_code: (price, uom)} 或 None
        price_layers: List[Layer] (utils.layered_resource) 或 None
        strict: True 时只加客户外挂层 providers (uploaded / ERPNext 不加)
        unit_corrections: ERPNext 过渡期单位修正 dict; None 用模块默认
                          BOM_UNIT_CORRECTIONS。仅在 UOM 校验通过后才应用。
        desired_uoms: dict {material_code: str} — BOM 该物料的消耗单位。
                      传入后 ERPNext 层按 desired-UOM 校验: 单位不匹配则视为缺口
                      (返回 None), 匹配或无 desired 时再应用 legacy 修正并返回价。
                      对齐 ttpos 按 desired-UOM 选行:
                      ttpos-bmp/.../stock/item.go:385 preferItemUnitCost

    Returns:
        Resolver。key = (material_code, material_name) tuple, value = float price.
        全栈未命中返回 None — 调用方按业务语义兜底 (bq_native / 0 / etc.)。

    注意: bq_native (per-row bq_price) 不进 Resolver, 因为它是行级而非全局。
    """
    if unit_corrections is None:
        unit_corrections = BOM_UNIT_CORRECTIONS

    providers = []

    # 1) 客户外挂层 (priority 来自 layer.priority, 通常 100+)
    for layer in price_layers or []:
        providers.append(MaterialPriceLayerProvider(
            name=layer.name,
            priority=layer.priority,
            data=layer.data,
            index_by=getattr(layer, "index_by", "code"),
        ))

    if not strict:
        # 2) 上传清单 (priority 80) — 大小写不敏感精确查
        if uploaded_prices:
            def _fetch_uploaded(key):
                code, _name = key
                if not code:
                    return None
                for k in (code, str(code).upper(), str(code).lower()):
                    if k in uploaded_prices:
                        return uploaded_prices[k]
                return None
            providers.append(CallableProvider(
                name="uploaded_price_list",
                priority=80,
                fetch=_fetch_uploaded,
            ))

        # 3) ERPNext (priority 50) — 按 desired-UOM 校验 + 过渡期 legacy 修正共存。
        #    对齐 ttpos 按 desired-UOM 选行: ttpos-bmp/.../stock/item.go:385 preferItemUnitCost
        if erp_prices:
            def _fetch_erp(key, _desired_uoms=desired_uoms,
                           _unit_corrections=unit_corrections):
                code, _name = key
                if not code:
                    return None
                for k in (code, str(code).upper(), str(code).lower()):
                    if k in erp_prices:
                        price, uom = erp_prices[k]
                        # 新机制: 若 desired_uoms 指定该物料的消耗单位且不匹配 → 缺口
                        want = (_desired_uoms or {}).get(code) or (_desired_uoms or {}).get(k)
                        if want and (uom or "").strip().lower() != want.strip().lower():
                            return None  # UOM 不匹配 = 缺口, 不充数; 见 Task 4.1 anchor
                        # 过渡期 legacy 修正: UOM 校验通过 (或无 desired) 后再 ÷ 除数。
                        # 待 desired-UOM 校验接进生产 (Phase 5/Task #6) 后退役此分支。
                        for corr_key in (code, str(code).upper(), str(code).lower()):
                            if corr_key in _unit_corrections:
                                price = price / _unit_corrections[corr_key]
                                break
                        return price
                return None
            providers.append(CallableProvider(
                name="ERPNext",
                priority=50,
                fetch=_fetch_erp,
            ))

    return Resolver(providers, name="material_unit_price")


def resolve_unit_price(
    material_code,
    bq_price,
    uploaded_prices,
    erp_prices,
    *,
    price_layers=None,
    strict=False,
    material_name=None,
    unit_corrections=None,
    desired_uoms=None,
    resolver=None,
):
    """返回 (单价, 来源名)。把"哪份数据给出单价"暴露给上层做审计.

    优先级 (高 → 低):
        1. price_layers   — 物料价 priority 栈 (支持按编码 / 按归一化名字索引)
        2. uploaded_prices — --price-list 上传清单 (按编码)
        3. erp_prices     — ERPNext Item Price (按 desired-UOM 校验)
        4. bq_price       — BQ ttpos_material 内置 (per-row, 不进 Resolver)

    strict=True 时只走 price_layers, 全栈未命中 → (0, '无 (strict)') 不再 fallback。

    unit_corrections: ERPNext 过渡期单位修正 dict; None 用模块默认 BOM_UNIT_CORRECTIONS;
                      仅在 desired-UOM 校验通过后应用 (透传给 build_material_price_resolver)。
    desired_uoms: dict {material_code: str} — BOM 该物料的消耗单位; 传给 build_material_price_resolver。

    性能: 调用方在 per-BOM-row 循环里调用时, 应在循环外用
    build_material_price_resolver 构造一次 resolver 传进来 — 否则每行都重建
    整个 Resolver (10 万行 = 重建 10 万次). 不传 resolver 时内部自建 (兼容).
    """
    if resolver is None:
        resolver = build_material_price_resolver(
            uploaded_prices, erp_prices, price_layers,
            strict=strict, unit_corrections=unit_corrections,
            desired_uoms=desired_uoms,
        )
    result = resolver.resolve((material_code, material_name))

    if result is not None:
        return result.value, result.source

    # 全栈未命中
    if strict:
        return 0.0, "无 (strict)"

    # BQ 缺省 (per-row bq_price, 不进 Resolver)
    return float(bq_price or 0), "bq_native"
