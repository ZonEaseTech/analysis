"""Builders — 把 yaml config / 既有 Layer 列表 构造成 Resolver。

两种入口:

  1. `build_resolver(name, sources_config, context)` — 从 yaml-loaded 配置构造
     (P3 fact_overrides 通用入口会大量用，每种事实在 resolvers.yaml 注册一段)

  2. `from_layers(name, layers)` / `from_layers_with_matcher(name, layers, matcher)`
     — 从 `semantic/resolvers.layered_resource.Layer` 列表桥接
     (P1 重构期 legacy 代码迁移到 Resolver 用)
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from .base import Resolver
from .providers import CallableProvider, DictProvider, YamlMatchProvider


def build_resolver(
    name: str,
    sources: list[dict],
    context: Optional[Mapping[str, Any]] = None,
) -> Resolver:
    """从配置列表构造 Resolver。

    每个 source 必须有 `kind` (dict | yaml_match | callable) + `name` + `priority`，
    其它字段视 kind 而定。yaml 里出现 callable / matcher 引用名时，从 context 里查。

    Args:
        name: Resolver 的逻辑名（"bom_qty" / "material_unit_price"）
        sources: 配置列表，例如:
            [
              {"kind": "dict", "name": "uploaded_list", "priority": 80,
               "data": {...}},
              {"kind": "yaml_match", "name": "market", "priority": 100,
               "data": {...}, "matcher": "bom_name_matcher"},
              {"kind": "callable", "name": "erpnext", "priority": 50,
               "fetch": "fetch_erp_price"},
            ]
        context: callable / matcher 命名查表 dict。yaml 里写字符串，运行时从这里查。
                 (yaml 不能直接序列化 callable，所以需要这个间接层)

    Returns:
        Resolver（providers 已按 priority 降序排好）
    """
    context = context or {}
    providers = []
    for src in sources:
        kind = src.get("kind", "dict")
        provider_name = src["name"]
        priority = int(src.get("priority", 0))

        if kind == "dict":
            providers.append(DictProvider(
                name=provider_name,
                priority=priority,
                data=src["data"],
            ))

        elif kind == "yaml_match":
            matcher = _resolve_ref(src["matcher"], context, kind="matcher")
            providers.append(YamlMatchProvider(
                name=provider_name,
                priority=priority,
                data=src["data"],
                matcher=matcher,
            ))

        elif kind == "callable":
            fetch = _resolve_ref(src["fetch"], context, kind="callable")
            providers.append(CallableProvider(
                name=provider_name,
                priority=priority,
                fetch=fetch,
            ))

        else:
            raise ValueError(
                f"Unknown provider kind: {kind!r} (source name={provider_name!r}). "
                "Supported: dict, yaml_match, callable. "
                "添加新 kind 请扩展 builder.py 并在文档说明。"
            )

    return Resolver(providers, name=name)


def from_layers(name: str, layers) -> Resolver:
    """Legacy bridge: 从 semantic/resolvers.layered_resource.Layer 列表构造 Resolver。

    用于 P1 迁移期 — 现有代码用 load_layers + lookup_layered 的，先桥接到 Resolver
    再逐步把 source 元数据吃透。

    要求每个 Layer 有 {name, priority, data} 三个属性。
    """
    providers = [
        DictProvider(name=L.name, priority=L.priority, data=L.data)
        for L in layers
    ]
    return Resolver(providers, name=name)


def from_layers_with_matcher(
    name: str,
    layers,
    matcher: Callable[[Any, Mapping], Optional[Any]],
) -> Resolver:
    """Legacy bridge with custom matcher (BOM 商品名模糊匹配场景用)。

    所有 layer 共享同一个 matcher。如果未来不同层要不同 matcher，再扩展。
    """
    providers = [
        YamlMatchProvider(
            name=L.name, priority=L.priority, data=L.data, matcher=matcher,
        )
        for L in layers
    ]
    return Resolver(providers, name=name)


# ───────────────────────────────────────────────────────────────
# 内部辅助
# ───────────────────────────────────────────────────────────────

def _resolve_ref(ref, context: Mapping, kind: str):
    """str → context.get(str) | callable → callable | else → error。"""
    if callable(ref):
        return ref
    if isinstance(ref, str):
        if ref not in context:
            raise KeyError(
                f"build_resolver: {kind} reference {ref!r} not in context. "
                f"Available: {list(context.keys())}"
            )
        return context[ref]
    raise TypeError(
        f"build_resolver: {kind} must be a str (context ref) or callable, "
        f"got {type(ref).__name__}."
    )
