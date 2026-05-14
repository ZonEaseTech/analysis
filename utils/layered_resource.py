"""通用 priority 栈外挂资源 —— 把"覆盖优先级"从"BOM"这个具体维度抽到通用层。

业务里每种事实 (BOM 数量/单位、物料单价、SKU 别名、套餐结构 …) 都可能有
多个来源 (客户给的 Excel、上传清单、ERPNext、BQ 原生)。每种事实应该:
  - 拥有自己的 priority 栈, 互不干涉
  - 命中时记录来源 (审计)
  - 一律 lookup 不到时按各自语义 fallback (报警/0/None)

这个文件提供:
  Layer            : 一层数据 (name, priority, dict)
  load_layers      : 从 config 读 source 列表 → List[Layer], priority 降序
  lookup_layered   : 按 key 从高到低试每层, 返回 (value, source_name)

调用方:
  - bq_reports/profit_margin_report._load_bom_layers (qty/unit)
  - bq_reports/profit_margin_report._load_material_price_layers (单价)
  - 任何想要 "事实层 + 优先级" 语义的新维度
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class Layer:
    name: str          # 来源标识 — 一律用源文件 basename, 客户能直接定位
    priority: int      # 数字大 = 更权威
    data: Dict[Any, Any]


def load_layers(
    sources: List[Dict[str, Any]],
    loader: Callable[[Dict[str, Any]], Dict[Any, Any]],
    label_prefix: str = "Resource",
) -> List[Layer]:
    """从 source 配置列表加载各层, 按 priority 降序返回。

    Args:
        sources: 每个 source 是 dict, 至少含 path/adapter, 可选 name/priority。
        loader: source_cfg → dict 的具体加载函数 (跟数据维度强相关)。
        label_prefix: 日志前缀, 方便区分多种栈一起加载时的输出。

    name 推导顺序: 显式 name > path 的 basename > "?"
        强制偏好文件名而非自编标签 — 客户 Excel 里看到 BOM来源 = "abc.xlsx",
        能直接 grep 找源文件; 看到 "fallback_layer_3" 就只能问开发者了。
    """
    import os
    layers: List[Layer] = []
    for src in sources or []:
        path = src.get("path", "")
        name = src.get("name") or (os.path.basename(path) if path else "?")
        priority = int(src.get("priority", 0))
        try:
            data = loader(src)
        except Exception as e:
            print(f"[{label_prefix}[{name}]] 加载失败: {e}")
            continue
        if data:
            layers.append(Layer(name=name, priority=priority, data=data))
    layers.sort(key=lambda L: -L.priority)
    if layers:
        order = " > ".join(f"{L.name}(p={L.priority}, {len(L.data)})" for L in layers)
        print(f"[{label_prefix} Layers] 优先级: {order}")
    return layers


def lookup_layered(
    layers: List[Layer],
    key: Any,
    matcher: Optional[Callable[[Any, Dict[Any, Any]], Optional[Any]]] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """按 priority 从高到低逐层试匹配, 命中即返回。

    Args:
        layers: load_layers 的输出 (priority 已降序)。
        key: 查找键 (SKU 名 / 物料编码 / 等)。
        matcher: 自定义匹配函数 (key, dict) → value or None。
                 默认行为 = layer.data.get(key)。BOM SKU 名匹配等需要
                 模糊匹配的, 传 _match_fallback_bom 之类的函数。

    Returns:
        (matched_value, source_name) 命中, 或 (None, None) 全栈未命中。
    """
    if key is None or not layers:
        return None, None
    for L in layers:
        if matcher is not None:
            v = matcher(key, L.data)
        else:
            v = L.data.get(key)
        if v is not None and v != [] and v != {}:
            return v, L.name
    return None, None
