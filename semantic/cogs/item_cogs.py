"""单品 / 套餐的 BOM 展开 — COGS 计算的核心 recipe.

来源: bq_reports/profit_margin_report.py:aggregate_with_bom 内联逻辑
(lines 1216-1268), 抽出为 semantic 层 public API。

输入: 一个 item_uuid + 该店 BOM / 套餐结构 + 单价解析函数
输出: 该商品(份) 消耗的物料明细 dict {material_code: (name, num, unit_price, uom)}

mode='combo' 套餐:
  - combo_structure[item_uuid] = [(child_uuid, child_num, weight), ...]
  - 子产品 BOM 物料量 = bom_num × child_num × weight
  - 同物料在多个子品里出现 → 累加 num, 保留首次的 unit_price + uom + name

mode='single' 单品:
  - 直接读 store_boms[item_uuid]
  - 同一商品多个 product_bom (不同 flavor 但共用 bom_card) → dedup 同 material_code,
    避免 N 倍虚增

`price_resolver` 是个 callable: (material_code, material_name) -> base_price (float)
通常由 semantic.cogs.material_price.build_material_price_resolver + Resolver.resolve
封装得到, 也可以传 lambda 自定义 (e.g. mock for tests).
"""
from __future__ import annotations

from typing import Callable, Optional


def expand_item_bom(
    item_uuid: str,
    mode: str,
    store_boms: dict,
    store_combo_struct: dict,
    price_resolver: Callable[[str, Optional[str]], float],
) -> dict:
    """展开一份商品消耗的 BOM 物料.

    Args:
        item_uuid: 商品 UUID (combo 模式下是套餐 UUID).
        mode: "combo" | "single".
        store_boms: {item_uuid: [(material_code, material_name, bom_num,
                                  bom_unit, conv_rate, bq_price), ...]}
        store_combo_struct: {combo_uuid: [(child_uuid, child_num, weight), ...]}
            JSON cache 把 tuple 序列化为 list, 两种都识别;
            旧 shape 纯字符串 child_uuid 兼容为 num=1, weight=1.
        price_resolver: callable (material_code, material_name) -> base_price (float).

    Returns:
        dict {material_code: (material_name, bom_num, unit_price, bom_unit)}.

        unit_price 已乘 conv_rate. bom_num 在 combo 模式下按 child_num × weight 加权。
    """
    result: dict = {}

    if mode == "combo":
        child_specs = store_combo_struct.get(item_uuid, [])
        for spec in child_specs:
            if isinstance(spec, (tuple, list)) and len(spec) == 3:
                child_uuid, child_num, weight = spec
            else:
                # 旧 shape: 纯字符串 child_uuid (synthetic 测试用)
                child_uuid, child_num, weight = spec, 1.0, 1.0
            child_mult = float(child_num) * float(weight)
            child_bom = store_boms.get(child_uuid, [])
            for (material_code, material_name, bom_num,
                 bom_unit, conv_rate, _bq_price) in child_bom:
                if not material_code:
                    continue
                base_price = price_resolver(material_code, material_name)
                unit_price = base_price * (conv_rate or 1)
                weighted_bom_num = bom_num * child_mult
                if material_code in result:
                    prev_name, prev_num, prev_up, prev_unit = result[material_code]
                    result[material_code] = (
                        prev_name or material_name,
                        prev_num + weighted_bom_num,
                        unit_price,                   # 同物料同单位价不变
                        prev_unit or bom_unit,
                    )
                else:
                    result[material_code] = (
                        material_name, weighted_bom_num, unit_price, bom_unit,
                    )
    else:
        # 单品: 直接匹配 BOM. 同 material_code 多条 → dedup, 取第一条.
        item_bom = store_boms.get(item_uuid, [])
        for (material_code, material_name, bom_num,
             bom_unit, conv_rate, _bq_price) in item_bom:
            if not material_code:
                continue
            if material_code not in result:
                base_price = price_resolver(material_code, material_name)
                unit_price = base_price * (conv_rate or 1)
                result[material_code] = (
                    material_name, bom_num, unit_price, bom_unit,
                )

    return result
