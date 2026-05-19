"""弧酷菜品 → BOM 匹配 (高置信度 only).

匹配顺序:
  1. SPELLING_ALIASES → strict (整 key / 中文段精确)
  2. normalize 候选名 → strict

不走 loose fuzzy (开头/包含/长前缀) — 误匹风险.
不走 '+' 拆解 — 市场已直接提供完整 BOM, 无需运行时拆解.

未匹的标 match_type='unmatched', 让市场决策.
"""
from __future__ import annotations

from typing import Optional, Tuple

from external_sales.huku.aliases import SPELLING_ALIASES
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


def match_item(item: str, bom_layers) -> Tuple[Optional[list], str, str]:
    """主匹配入口.

    Returns:
        (bom_records or None, match_type, bom_src)
        match_type ∈ {'strict','spelling','normalize','unmatched'}
    """
    mapped = SPELLING_ALIASES.get(item, item)
    bom, src = strict_match(mapped, bom_layers)
    if bom:
        mt = "spelling" if mapped != item else "strict"
        return bom, mt, src

    for c in normalize_item_name(mapped):
        # normalize 后再 apply SPELLING — 解决"剥(TH)后才命中 alias"
        # 例: 脆皮手枪鸡腿饭(TH) → normalize 剥(TH) → 脆皮手枪鸡腿饭 → SPELLING → 脆皮手枪腿饭
        c2 = SPELLING_ALIASES.get(c, c)
        bom, src = strict_match(c2, bom_layers)
        if bom:
            return bom, "normalize", src

    # '+' 拆解兜底 — 仅当所有成分均 strict 命中才返回 split (高置信)
    # 任一成分匹不上 → 返回 unmatched, 不输出部分拼凑的 BOM (避免低质量数据)
    if "+" in item:
        base = strip_th_suffix(item)
        parts = base.split("+")
        comp_results = []
        all_matched = True
        for qty, comp_name in (parse_combo_part(p) for p in parts):
            mapped_n = SPELLING_ALIASES.get(comp_name, comp_name)
            comp_bom, _ = strict_match(mapped_n, bom_layers)
            if comp_bom is None:
                for c in normalize_item_name(mapped_n):
                    c2 = SPELLING_ALIASES.get(c, c)
                    comp_bom, _ = strict_match(c2, bom_layers)
                    if comp_bom: break
            if comp_bom is None:
                all_matched = False
                break
            comp_results.append((qty, comp_bom))
        if all_matched:
            merged = {}
            for qty, bl in comp_results:
                for code, mname, q, unit in bl:
                    nq = q * qty
                    if code in merged:
                        pn, pq, pu = merged[code]
                        merged[code] = (pn or mname, pq + nq, pu or unit)
                    else:
                        merged[code] = (mname, nq, unit)
            composed = [(c, n, q, u) for c, (n, q, u) in merged.items()]
            return composed, "split", "+ 拆解"

    return None, "unmatched", "无"
