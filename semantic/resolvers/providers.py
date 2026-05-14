"""Three built-in Provider implementations covering the common cases.

  - **DictProvider**: 最简单，对 dict 做 exact-key lookup。BQ 内置 BOM、
    上传清单（按 material_code）、ERPNext 价格表 全部能直接套。

  - **YamlMatchProvider**: 带自定义 matcher 的 lookup。BOM 按"商品名"匹配
    必须用这个（中文段精确 → 模糊前缀 → 长前缀 → 包含 多层匹配）。matcher
    自己负责返回"匹配到的 key"，Provider 拿 key 去 data dict 里取值。

  - **CallableProvider**: 包装一个 callable，按需 fetch。用于:
      - ERPNext API（懒加载，避免预拉全表）
      - 数据库查询
      - 计算型 fallback（"该字段没填，按公式补"）

任何不在这三种范式里的需求，写一个新 dataclass 满足 Provider Protocol 就行——
不需要继承。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Mapping, Optional, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class DictProvider(Generic[T]):
    """Exact-key dict lookup. 最常见的 Provider。

    `data` 是任何支持 `.get(key)` 的 Mapping (dict, ChainMap, etc.)。
    """

    name: str
    priority: int
    data: Mapping[Any, T]

    def get(self, key: Any) -> Optional[T]:
        return self.data.get(key)


@dataclass(frozen=True)
class YamlMatchProvider(Generic[T]):
    """Fuzzy lookup via custom matcher function.

    用于 BOM 按"商品名"匹配场景：data 里 key 是规范化的商品名（带规格 / 中文段），
    传入的 key 可能是 BQ 商品名简写。matcher 自己实现优先级（精确 > 模糊前缀 > 包含），
    返回匹配到的 data key 或 None。

    Args:
        data: { matched_key: value, ... }
        matcher: (query_key, data) -> matched_key | None
                 (query_key, data) 两个参数让 matcher 不需要捕获状态，纯函数易测。
    """

    name: str
    priority: int
    data: Mapping[str, T]
    matcher: Callable[[Any, Mapping[str, T]], Optional[str]]

    def get(self, key: Any) -> Optional[T]:
        matched = self.matcher(key, self.data)
        if matched is None:
            return None
        return self.data.get(matched)


@dataclass(frozen=True)
class CallableProvider(Generic[T]):
    """Lazy / API-backed Provider.

    Args:
        fetch: (key) -> value | None。返回 None 表示"这里没有，让 Resolver 试下一个"。

    用法举例：
        # 包装 ERPNext API（已带缓存）
        erp_provider = CallableProvider(
            name="erpnext_api",
            priority=20,
            fetch=lambda code: erpnext_api.get_item_price(code),
        )

        # 计算型 fallback（"如果都没值，按公式估"）
        fallback = CallableProvider(
            name="fallback_estimate",
            priority=-10,
            fetch=lambda code: estimate_unit_price(code) if is_food_item(code) else None,
        )
    """

    name: str
    priority: int
    fetch: Callable[[Any], Optional[T]]

    def get(self, key: Any) -> Optional[T]:
        return self.fetch(key)
