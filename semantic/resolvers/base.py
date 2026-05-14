"""Resolver kernel — Provider Protocol + Resolved + Resolver.

设计选择 worth knowing before changing:

  - **Provider 是 Protocol 不是 ABC**: duck typing 友好。任何对象只要带
    {name: str, priority: int, get(key) -> Optional[T]} 三件套就能塞进 Resolver，
    不用强制继承。这跟 Python 标准库的 `Iterable` / `Sized` 一个套路。

  - **Resolver 构造时排序，运行期只读**: 保证 resolve() 是无 side effect 的纯查询，
    可以安全并发。同 priority 的 provider 按 insertion order 稳定排序。

  - **resolve() 返回 Optional[Resolved], 不是 Resolved with None value**: 全栈
    未命中是个明确语义，跟"命中但值是 None"分开。caller 可以选 resolve()（要严格
    判 None）或 resolve_or_default()（要兜底）。

  - **Resolved 带 source + priority 元数据**: 为 P2 source-aware validator 准备。
    任何报表行带上 source_map = {field: resolved.source}, validator 失败时能直接
    指认"差 ¥20 在 revenue@market_20260513 这条源"。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Optional, Protocol, TypeVar, runtime_checkable


T = TypeVar("T")


@runtime_checkable
class Provider(Protocol[T]):
    """A data source contributing to a priority-stacked Resolver.

    Implementations:
      - DictProvider          : exact-key lookup in a dict
      - YamlMatchProvider     : fuzzy match via custom matcher (e.g., SKU name)
      - CallableProvider      : lazy / API-backed lookup

    Custom Provider 实现：写一个带 name/priority/get 的 class 即可，不需要继承。
    """

    name: str         # 来源标识 — 用源文件 basename 或服务名，客户能直接定位
    priority: int     # 数字大 = 更权威 (e.g. 100 = 客户手工核对版, 0 = BQ 原生)

    def get(self, key: Any) -> Optional[T]:
        """Lookup. None means "not here, try next provider". 命中返回 value。"""
        ...


@dataclass(frozen=True)
class Resolved(Generic[T]):
    """One successful lookup carrying its audit trail.

    Always populated when Resolver.resolve() hits any provider. Carries
    `source` + `priority` so downstream (audit columns / validator) knows
    which provider's data made it into the final report.
    """

    value: T
    source: str       # provider.name; "无" if missing-default sentinel
    priority: int     # provider.priority; -1 for synthetic defaults


class Resolver(Generic[T]):
    """Priority-stacked multi-provider lookup.

    Args:
        providers: any iterable of Provider[T]; order doesn't matter (sorted
            here by -priority, stable on ties).
        name: human-readable identifier for repr / logging (e.g. "bom_qty",
            "material_unit_price"). Doesn't affect behavior.

    Usage:
        r = Resolver([
            DictProvider(name="market_20260513", priority=100, data={...}),
            DictProvider(name="erpnext_pull",    priority=50,  data={...}),
            DictProvider(name="bq_native",       priority=0,   data={...}),
        ], name="material_unit_price")

        result = r.resolve("MK01018")
        if result is None:
            ...  # 全栈未命中，按业务语义兜底
        else:
            print(result.value, "from", result.source)
    """

    def __init__(self, providers, name: str = ""):
        # 按 priority 降序稳定排序；同 priority 保留插入顺序
        self._providers: list[Provider[T]] = sorted(
            providers, key=lambda p: -int(p.priority)
        )
        self.name = name

    def resolve(self, key: Any) -> Optional[Resolved[T]]:
        """从高 priority 到低逐个尝试。命中即返回；全栈未命中返回 None。

        命中条件: provider.get(key) 返回 not-None。这意味着 Provider 自己负责
        判定"是否命中"——包括 dict 没这个 key、模糊匹配失败、API 返回空等。

        **动态 source 支持**: provider.get() 可以返回:
          - plain value T：Resolver 包成 Resolved(value=v, source=provider.name, ...)
          - Resolved[T] 对象：Resolver 直接转发（让 Provider 自己决定 source/priority）

        后者用于"同一 provider 内部命中条目带不同来源标签"的场景，例如:
        客户外挂物料价单层里同时有泰国采版和进口版，命中时 source 要带 [source_tag]
        后缀区分。
        """
        for p in self._providers:
            v = p.get(key)
            if v is None:
                continue
            if isinstance(v, Resolved):
                return v  # Provider 自带完整 source/priority 元数据
            return Resolved(value=v, source=p.name, priority=int(p.priority))
        return None

    def resolve_or_default(
        self,
        key: Any,
        default: T,
        default_source: str = "default",
    ) -> Resolved[T]:
        """命中返回真实 Resolved；未命中返回带 default value 的 Resolved。

        提供 default_source 让审计列也能显示"用了兜底值"。priority=-1 标记
        合成 Resolved (区别于真实 provider)。
        """
        r = self.resolve(key)
        if r is not None:
            return r
        return Resolved(value=default, source=default_source, priority=-1)

    @property
    def providers(self) -> list[Provider[T]]:
        """Read-only view of underlying providers (already sorted by priority desc)."""
        return list(self._providers)

    def __repr__(self) -> str:
        order = " > ".join(
            f"{p.name}(p={p.priority})" for p in self._providers
        ) or "<empty>"
        return f"Resolver<{self.name}>({order})"

    def __len__(self) -> int:
        return len(self._providers)

    def __bool__(self) -> bool:
        return bool(self._providers)
