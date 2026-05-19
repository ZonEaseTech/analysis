"""弧酷菜品 → BOM 匹配 (高置信度 only).

只走 fuzzy 5 层的第 1-2 层 (整 key 精确 / 中文段精确) + 已确认 alias.
loose fuzzy (开头/包含/长前缀) 不走 — 误匹风险.

未匹的标 match_type='unmatched', 让市场决策.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from external_sales.huku.aliases import INGREDIENT_ALIASES, SPELLING_ALIASES
from external_sales.huku.normalize import (
    normalize_item_name,
    parse_combo_part,
    strip_th_suffix,
)


def strict_match(name: str, bom_layers):
    """严格匹配 — fuzzy 5 层中的第 1-2 层 (整 key / 中文段精确)."""
    if not name: return None, None
    n = name.strip()
    for layer in bom_layers or []:
        if len(layer) == 4:
            name_layer, _priority, boms, _mm = layer
        else:
            name_layer, _priority, boms = layer
        if n in boms:
            return boms[n], name_layer
        for k, v in boms.items():
            zh = k.split(" / ")[0].strip()
            if zh == n:
                return v, name_layer
    return None, None


def try_match_component(name: str, bom_layers):
    """匹配 '+ 组合' 的单个成分. 顺序: SPELLING → strict → normalize+strict → INGREDIENT_ALIAS.

    Returns:
        (bom_records: list[(code,mname,qty,unit)], src: str) or (None, None)
    """
    mapped = SPELLING_ALIASES.get(name, name)
    bom, src = strict_match(mapped, bom_layers)
    if bom: return bom, src
    for c in normalize_item_name(mapped):
        bom, src = strict_match(c, bom_layers)
        if bom: return bom, src
    if mapped in INGREDIENT_ALIASES:
        code, mname, q, unit = INGREDIENT_ALIASES[mapped]
        return [(code, mname, q, unit)], "ingredient_alias"
    return None, None


def match_item(item: str, bom_layers) -> Tuple[Optional[list], str, str]:
    """主匹配入口. 顺序:
       1. SPELLING_ALIASES → strict
       2. normalize → strict
       3. '+' 拆解 — 所有成分均匹中 (含 INGREDIENT_ALIAS)
       Returns:
         (bom_records or None, match_type: str, bom_src: str)
         match_type ∈ {'strict','spelling','normalize','split','unmatched'}
    """
    # 1. SPELLING + strict
    mapped = SPELLING_ALIASES.get(item, item)
    bom, src = strict_match(mapped, bom_layers)
    if bom:
        mt = "spelling" if mapped != item else "strict"
        return bom, mt, src

    # 2. normalize + strict
    for c in normalize_item_name(mapped):
        bom, src = strict_match(c, bom_layers)
        if bom:
            return bom, "normalize", src

    # 3. '+' 拆解
    if "+" in item:
        base = strip_th_suffix(item)
        parts = base.split("+")
        parsed = [parse_combo_part(p) for p in parts]
        comp_results = []
        unmatched_parts = []
        for qty, comp_name in parsed:
            comp_bom, _comp_src = try_match_component(comp_name, bom_layers)
            if comp_bom:
                comp_results.append((qty, comp_name, comp_bom))
            else:
                unmatched_parts.append((qty, comp_name))
        if not unmatched_parts:
            merged = {}
            for qty, _n, bl in comp_results:
                for code, mname, q, unit in bl:
                    new_q = q * qty
                    if code in merged:
                        pn, pq, pu = merged[code]
                        merged[code] = (pn or mname, pq + new_q, pu or unit)
                    else:
                        merged[code] = (mname, new_q, unit)
            composed = [(c, n, q, u) for c, (n, q, u) in merged.items()]
            return composed, "split", "+ 拆解"

    return None, "unmatched", "无"
