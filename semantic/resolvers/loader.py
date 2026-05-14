"""YAML-driven Resolver loader — P3 fact_overrides 通用入口。

让"市场扔新表给我们调账" = 改 YAML 4 行, 不改 Python 代码:

  resources/wallace.*/resolvers.yaml:
    resolvers:
      commission_rate:                   # ← 类别名，必须在白名单里
        - kind: dict
          name: defaults
          priority: 0
          data: {grab: 0.30, lineman: 0.25, shopee: 0.20}
        - kind: dict
          name: hr_negotiated_20260513
          priority: 100
          path: ./commission_overrides.xlsx   # ← 外挂表
          adapter: excel
          mapping:
            ...

报表脚本调用:

  from semantic.resolvers import load_resolvers_from_yaml

  resolvers = load_resolvers_from_yaml(
      "resources/wallace.20260513/resolvers.yaml",
      allowed_categories=["commission_rate", "labor_cost"],
      fetchers={...},   # callable kind 命名查表 (如 ERPNext API)
      matchers={...},   # yaml_match kind 命名 matcher (如 BOM 模糊匹配)
  )

  commission = resolvers["commission_rate"].resolve("grab")
  # → Resolved(value=0.30, source="hr_negotiated_20260513", priority=100)

业务安全:
  allowed_categories 白名单确保只接受预定的 category, 防止 yaml 配置乱改业务规则.
  新增 category 时必须先在报表脚本里把它加入白名单, 二段 review (代码 + 配置).

设计要点:
  - 跟现有 _load_bom_layers / _load_material_price_layers **共存**, 不强制替换.
    后者继续返回 Layer 列表 (BOM 模糊匹配等已建好的复杂场景仍用它).
    本 loader 用于新场景 (P3.5 P&L 抽佣率 / 人力数据 / 调账等).
  - 找不到的 category 不在返回 dict 里, caller 自己判 (避免误以为 0 priority 默认).
  - kind=callable 不在 yaml 里序列化, 通过 fetchers 命名引用 (yaml 不能存 callable).
"""
from __future__ import annotations

import os
from typing import Any, Callable, Mapping, Optional

import yaml

from utils.resource_adapter import get_adapter
from .base import Resolver
from .providers import CallableProvider, DictProvider, YamlMatchProvider


def load_resolvers_from_yaml(
    yaml_path: str,
    allowed_categories: list[str],
    fetchers: Optional[Mapping[str, Callable]] = None,
    matchers: Optional[Mapping[str, Callable]] = None,
) -> dict[str, Resolver]:
    """从 yaml 配置加载多个 Resolver, 用 allowed_categories 白名单过滤。

    yaml schema (顶层 key 是 'resolvers'):
        resolvers:
          <category_name>:
            - kind: dict | yaml_match | callable
              name: <provider 来源标识>
              priority: <int, 大=权威>
              # kind=dict: data 二选一
              data: <inline dict>            # 或
              path: <str>
              adapter: <adapter name>        # 必须返回 dict
              # kind=yaml_match: 同上 + matcher 命名引用
              matcher: <matchers dict 里的 key>
              # kind=callable: fetch 命名引用
              fetch: <fetchers dict 里的 key>

    Args:
        yaml_path: 配置文件路径. 不存在则返回空 dict (报表能正常跑, 走默认逻辑)
        allowed_categories: 业务白名单. 防止市场扔的 yaml 改任意类别.
                            只有在这个列表里的 category 会被加载.
        fetchers: callable kind 的命名 callable 字典. 不传 / 没匹配 → 抛 KeyError.
        matchers: yaml_match kind 的命名 matcher 字典. 不传 / 没匹配 → 抛 KeyError.

    Returns:
        dict[category, Resolver]. 找不到的 category 不在 dict 里.

    Raises:
        ValueError: yaml 格式错 / kind 不识别
        KeyError: callable / matcher 引用不存在
    """
    if not os.path.exists(yaml_path):
        return {}

    with open(yaml_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    resolvers_cfg = cfg.get("resolvers", {}) or {}
    fetchers = dict(fetchers or {})
    matchers = dict(matchers or {})

    out: dict[str, Resolver] = {}

    for category, sources in resolvers_cfg.items():
        if category not in allowed_categories:
            # 业务安全: 静默忽略未授权类别. 不抛错避免新业务接入时 yaml 一改全挂.
            # 调试时打开 log 看 "Skipping category {category} (not in whitelist)".
            continue

        providers = []
        for src in sources or []:
            providers.append(_build_provider(src, category, fetchers, matchers))

        out[category] = Resolver(providers, name=category)

    return out


def _build_provider(
    src: dict,
    category: str,
    fetchers: Mapping[str, Callable],
    matchers: Mapping[str, Callable],
):
    """从单个 source config 构造 Provider."""
    kind = src.get("kind", "dict")
    name = src.get("name") or _default_name(src)
    priority = int(src.get("priority", 0))

    if kind == "dict":
        data = _load_data(src, category, name)
        return DictProvider(name=name, priority=priority, data=data)

    if kind == "yaml_match":
        data = _load_data(src, category, name)
        matcher_ref = src.get("matcher")
        if matcher_ref is None:
            raise ValueError(
                f"yaml_match source needs 'matcher' field (category={category}, source={name})"
            )
        if callable(matcher_ref):
            matcher = matcher_ref
        elif isinstance(matcher_ref, str):
            if matcher_ref not in matchers:
                raise KeyError(
                    f"yaml_match references matcher {matcher_ref!r} but it's not in matchers dict "
                    f"(category={category}, source={name}). Available: {list(matchers.keys())}"
                )
            matcher = matchers[matcher_ref]
        else:
            raise TypeError(f"matcher must be str or callable, got {type(matcher_ref).__name__}")
        return YamlMatchProvider(name=name, priority=priority, data=data, matcher=matcher)

    if kind == "callable":
        fetch_ref = src.get("fetch")
        if fetch_ref is None:
            raise ValueError(
                f"callable source needs 'fetch' field (category={category}, source={name})"
            )
        if callable(fetch_ref):
            fetch = fetch_ref
        elif isinstance(fetch_ref, str):
            if fetch_ref not in fetchers:
                raise KeyError(
                    f"callable references fetch {fetch_ref!r} but it's not in fetchers dict "
                    f"(category={category}, source={name}). Available: {list(fetchers.keys())}"
                )
            fetch = fetchers[fetch_ref]
        else:
            raise TypeError(f"fetch must be str or callable, got {type(fetch_ref).__name__}")
        return CallableProvider(name=name, priority=priority, fetch=fetch)

    raise ValueError(
        f"Unknown provider kind {kind!r} (category={category}, source={name}). "
        "Supported: dict, yaml_match, callable."
    )


def _load_data(src: dict, category: str, source_name: str) -> Mapping:
    """从 source config 加载 data dict.

    优先级:
      1. inline data (yaml 里直接写)
      2. path + adapter (用 utils/resource_adapter 加载, adapter 必须返回 dict)
    """
    if "data" in src:
        data = src["data"]
        if not isinstance(data, Mapping):
            raise TypeError(
                f"inline 'data' must be a dict/mapping, got {type(data).__name__} "
                f"(category={category}, source={source_name})"
            )
        return data

    if "path" in src and "adapter" in src:
        adapter_name = src["adapter"]
        adapter = get_adapter(adapter_name)
        records = adapter.load(src)
        if isinstance(records, Mapping):
            return records
        # adapter 返回 list 时, 这里不自动转 dict — 用户应该用专门的 adapter
        # 或者在 source config 里指定 'key_field' 让 loader 做转换 (未来扩展)
        raise TypeError(
            f"Adapter {adapter_name!r} returned {type(records).__name__}, expected dict. "
            f"(category={category}, source={source_name})\n"
            "提示: 写一个返回 dict 的 adapter, 或用 inline data."
        )

    raise ValueError(
        f"Source must have 'data' (inline) or 'path' + 'adapter' (external): "
        f"category={category}, source={source_name}"
    )


def _default_name(src: dict) -> str:
    """没显式 name 时, 用 path basename 或 'unnamed'."""
    if "path" in src:
        return os.path.basename(src["path"])
    return "unnamed"
