"""Multi-source priority-stacked resolution layer.

业务里同一份事实（BOM 数量、物料单价、套餐结构、调账数据）经常有多个来源:
客户给的 Excel、上传清单、ERPNext、BQ 原生。这一层把"用哪个"从硬编码
if/else 抽出来，让多源裁决变成数据驱动 + 来源可审计。

Layer relationships:
  - Provider 协议: 一个数据源能做什么 (name / priority / get(key))
  - Resolver: 按 priority 栈对一类事实裁决；返回 Resolved(value, source, priority)
  - Builder: 从 yaml 配置或现有 Layer 列表构造 Resolver

Used by (待迁移):
  - profit_margin_report: BOM 数量 + 物料单价 (现在用 _match_bom_layered /
    _resolve_unit_price_with_source 各自一套；P1 之后收敛到 Resolver)
  - profit_by_price: 同上
  - 未来 (P3): combo 结构、营收调账、店属性、其它 fact_overrides

Replaces (gradually, not immediately): utils/layered_resource.{Layer, load_layers,
lookup_layered}。后者保留作为底层 loader 工具，但裁决语义移到 Resolver。

设计要点 (不要随便改):
  - Resolver 不可变；构造时按 priority 降序排好 providers，之后只读
  - Provider 是 Protocol 不是抽象基类，duck typing 友好 — 任何带
    {name, priority, get(key)} 的类都能塞进去
  - Resolved 永远带 source + priority 元数据，方便 P2 source-aware validator 消费
  - 全栈未命中返回 None (不是 Resolved with None value) — 让 caller 显式处理
"""
from .base import Provider, Resolved, Resolver
from .providers import DictProvider, YamlMatchProvider, CallableProvider
from .builder import build_resolver, from_layers, from_layers_with_matcher
from .loader import load_resolvers_from_yaml
from .layered_resource import Layer, load_layers

__all__ = [
    "Provider", "Resolved", "Resolver",
    "DictProvider", "YamlMatchProvider", "CallableProvider",
    "build_resolver", "from_layers", "from_layers_with_matcher",
    "load_resolvers_from_yaml",
    "Layer", "load_layers",
]
