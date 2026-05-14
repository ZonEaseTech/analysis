"""COGS (Cost of Goods Sold) 语义子层.

把"商品成本怎么算"从 bq_reports/profit_margin_report.py 抽出来, 让未来报表
直接 import, 不再复制粘贴。

入口:
  - bom_match    — 商品名 → BOM 记录的多层模糊匹配
  - material_price — 物料编码 / 名字 → 单价的多层 priority 解析
  - item_cogs    — 单品 / 套餐的 BOM 展开 (combo 子品加权, 单品 dedup)

设计原则:
  - 纯函数为主, 不读全局配置 / 不直接连 BQ; 调用方负责加载数据
  - 跟 semantic/resolvers/ 共享 Provider/Resolver 协议
  - 跟 semantic/entities/{bom,combo}.py 互补 (entities 给 SQL CTE,
    cogs 给基于 Python dict 的计算)
"""

from semantic.cogs.bom_match import (
    build_bom_resolver,
    exact_match_bom_key,
    find_matched_bom_key,
    match_bom_layered,
    match_fallback_bom,
)
from semantic.cogs.item_cogs import expand_item_bom
from semantic.cogs.material_price import (
    BOM_UNIT_CORRECTIONS,
    MaterialPriceLayerProvider,
    build_material_price_resolver,
    resolve_unit_price,
)

__all__ = [
    # bom_match
    "build_bom_resolver",
    "exact_match_bom_key",
    "find_matched_bom_key",
    "match_bom_layered",
    "match_fallback_bom",
    # material_price
    "BOM_UNIT_CORRECTIONS",
    "MaterialPriceLayerProvider",
    "build_material_price_resolver",
    "resolve_unit_price",
    # item_cogs
    "expand_item_bom",
]
