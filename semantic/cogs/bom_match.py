"""商品名 → BOM 记录的多层模糊匹配.

来源: bq_reports/profit_margin_report.py 私有函数 (_find_matched_bom_key 等),
已抽出为 semantic 层 public API。

为什么不能用精确等值: 客户给的 fallback BOM key 是"中文名 / 英文名"双语
组合, 跟商品中文名不一致; 还有"鸡块（中）"vs"鸡块"这种细分。

5 层匹配优先级 (从严到宽), 都失败返回 None:
  1. 整 key 精确等值
  2. 中文段 ('/' 首段) 精确等值
  3. 中文段以 item_name 开头
  4. 中文段包含 item_name
  5. 长前缀模糊 (item ≥5 字符, 前 10 字符出现在 zh)

任一层多候选 → 取**中文段最短**的 (最接近原始商品名)。
"""
from __future__ import annotations

from semantic.resolvers import Resolver, YamlMatchProvider


def find_matched_bom_key(item_name, fallback_boms):
    """5 层模糊匹配 — 返回**匹配到的 key**, 不返回 value.

    Args:
        item_name: 商品中文名 (来自 ttpos_order_product.product_name)
        fallback_boms: dict {key: bom_records}, key 形如 "鸡块 / Chicken Nuggets".

    Returns:
        匹配到的 key (str) 或 None.

    历史 bug: 旧逻辑遍历 dict 直接 `item_name in key` 会优先命中长 key,
    导致"鸡肉芝士球"被错误匹配到"周一特惠 - 鸡肉芝士球 2 盒 69",
    成本按 2 盒装算 (虚高一倍)。本实现按"中文段最短"打破平局规避。
    """
    if not item_name or not fallback_boms:
        return None
    name = item_name.strip()
    if not name:
        return None

    # 1. 整 key 精确
    if name in fallback_boms:
        return name

    # 预提取中文首段
    keys_zh = [(k, k.split(" / ")[0].strip()) for k in fallback_boms]

    # 2. 中文段精确
    for k, zh in keys_zh:
        if zh == name:
            return k

    # 3. 中文段以 item_name 开头
    starts = [(k, zh) for k, zh in keys_zh if zh.startswith(name) and zh != name]
    if starts:
        starts.sort(key=lambda x: len(x[1]))
        return starts[0][0]

    # 4. 中文段包含 item_name
    contains = [(k, zh) for k, zh in keys_zh if name in zh]
    if contains:
        contains.sort(key=lambda x: len(x[1]))
        return contains[0][0]

    # 5. 长前缀模糊
    if len(name) >= 5:
        prefix = name[:10]
        loose = [(k, zh) for k, zh in keys_zh if prefix in zh]
        if loose:
            loose.sort(key=lambda x: len(x[1]))
            return loose[0][0]

    return None


def match_fallback_bom(item_name, fallback_boms):
    """便利接口 — 委托给 find_matched_bom_key, 返回 matched value.

    新代码建议用 find_matched_bom_key + 自己 dict[key], 或者用 Resolver。
    """
    matched_key = find_matched_bom_key(item_name, fallback_boms)
    if matched_key is None:
        return None
    return fallback_boms[matched_key]


def exact_match_bom_key(item_name, boms):
    """精确匹配 — 只认整 key 完全相等, 不做任何模糊.

    给"市场/客户精确列出商品名"的 BOM 层用 (e.g. 补充 BOM): 那些 key 就是
    从报表 copy 出来的精确商品名, 用模糊匹配反而会让短名 ("鸡块") 误命中
    长 key ("鸡块（电影票折扣）").
    """
    if not item_name or not boms:
        return None
    name = item_name.strip()
    return name if name in boms else None


def build_bom_resolver(bom_layers):
    """从 bom_layers (tuple 列表) 构造 BOM Resolver.

    Args:
        bom_layers: [(name, priority, boms_dict), ...] 或
                    [(name, priority, boms_dict, match_mode), ...]
            name       — 层名 (写入 Resolved.source, 用于审计)
            priority   — int, 越大越优先
            boms_dict  — {key: bom_records}, key 同 matcher 输入
            match_mode — "fuzzy" (默认, 5 层模糊) | "exact" (只整 key 精确)
                         省略时为 "fuzzy", 向后兼容 3 元组.

    Returns:
        Resolver[list[bom_record_tuple]], key = item_name (str),
        value = matched BOM records list。
    """
    providers = []
    for layer in (bom_layers or []):
        if len(layer) == 4:
            name, priority, boms, match_mode = layer
        else:
            name, priority, boms = layer
            match_mode = "fuzzy"
        matcher = exact_match_bom_key if match_mode == "exact" else find_matched_bom_key
        providers.append(YamlMatchProvider(
            name=name, priority=priority, data=boms, matcher=matcher,
        ))
    return Resolver(providers, name="bom_qty")


def match_bom_layered(item_name, bom_layers):
    """按 priority 从高到低逐层尝试匹配.

    Returns:
        (matched_list, layer_name) 或 (None, None).

    内部走 build_bom_resolver + Resolver.resolve, 自动按 priority 重排,
    不依赖输入预排序。
    """
    if not item_name or not bom_layers:
        return None, None
    resolver = build_bom_resolver(bom_layers)
    result = resolver.resolve(item_name)
    if result is None:
        return None, None
    return result.value, result.source
