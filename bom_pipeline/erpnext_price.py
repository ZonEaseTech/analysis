#!/usr/bin/env python3
"""
ERPNext 物料采购成本复刻 — calculateFinalItemUnitCost
=====================================================
Python 复刻 ttpos-server-go 后端的物料采购单价口径，确保我们出的成本毛利表
跟 ttpos 后端 GetItemUnitCost 走同一条算法，避免两边口径漂移。

后端真源:
  ttpos-server-go/ttpos-bmp/app/ttpos-erp/internal/logic/stock/item.go
    - calculateFinalItemUnitCost(baseCost, rules, taxRate)
    - applyItemUnitCostPricingRules(baseCost, rules)
    - resolveItemUnitCostTaxRate(netCost, taxes, templateRates)
    - appliesToItemUnitCost(rule, priceList) — 条件判定（见 rule_applies）

口径（与 Go 逐行对齐）:
  净价  netCost = baseCost 依次套用 ERPNext buying Pricing Rules
                 (MarginType=Percentage → ×(1+rate/100); Amount → +amount)
  终价  若 netCost==0 或 taxRate==0 → 返回 netCost
        否则 → netCost × (1 + taxRate/100)
  税率  物料未配 Item Tax 时按泰国 VAT 7% 兜底 (defaultItemUnitCostTaxRate)

baseCost / UOM 换算说明:
  baseCost = ERPNext Item Price 表 price_list_rate（按匹配 UOM 取，Buying 价表）。
  UOM 换算与 Item Price 拉取在上游完成（实时拉取复用 bq_reports/utils/erpnext_api.py，
  口径见 docs/bom-pipeline.md）。本模块只负责"基价 → 终价"这段纯算法，可单测、可离线复跑。

验收基准:
  对 bom_with_erp_price_v4.xlsx 的每一行，用 (基价原始, 适用税率%) 经本模块算出的终价
  与 ERPNext新单价 列逐行一致（4465/4465）。见 enrich_bom_prices + 文末自检。
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

# 物料未配置 Item Tax 时的兜底税率（泰国 VAT 7%），对齐 Go defaultItemUnitCostTaxRate
DEFAULT_TAX_RATE = 7

# ERPNext buying 默认 Pricing Rule：Buying 价表统一加价 5%（Percentage）。
# v4 数据反推 + 后端 loadItemUnitCostPricingRules 取的就是这一条；如后端规则变更，改这里。
DEFAULT_BUYING_RULE = ("Percentage", 5.0)


@dataclass(frozen=True)
class PricingRule:
    """对齐 Go itemUnitCostPricingRule 的可用字段。

    字段顺序：(margin_type, margin_rate_or_amount, for_price_list, buying, disabled)
    新字段均有默认值，PricingRule(*DEFAULT_BUYING_RULE) 仍可构造（向后兼容）。
    """
    margin_type: str          # "Percentage" | "Amount"（大小写不敏感，其它值跳过）
    margin_rate_or_amount: float
    for_price_list: str = ""  # 空字符串 = 匹配任意价表（对齐 Go 空字符串语义）
    buying: bool = True       # 仅 buying=True 的规则才纳入成本计算
    disabled: bool = False    # 停用规则不套用


# ---------------------------------------------------------------------------
# rule_applies — 对齐 ttpos appliesToItemUnitCost
# 真源: ttpos-bmp/app/ttpos-erp/internal/logic/stock/item.go:279-296
# ---------------------------------------------------------------------------
def rule_applies(rule: PricingRule, price_list: str | None) -> bool:
    """复刻 appliesToItemUnitCost：buying && !disabled && 价表名大小写不敏感匹配。

    for_price_list 为空字符串时匹配任意价表（对齐 Go 空字符串语义）。
    """
    if not rule.buying or rule.disabled:
        return False
    if rule.for_price_list and rule.for_price_list.lower() != (price_list or "").lower():
        return False
    return True


def apply_pricing_rules(base_cost: float, rules: list[PricingRule]) -> float:
    """复刻 applyItemUnitCostPricingRules：依次套用 margin 规则。"""
    cost = base_cost
    for rule in rules:
        mt = (rule.margin_type or "").lower()
        if mt not in ("percentage", "amount"):
            continue
        if mt == "percentage":
            cost *= 1 + rule.margin_rate_or_amount / 100
        else:  # amount
            cost += rule.margin_rate_or_amount
    return cost


def calculate_final_item_unit_cost(
    base_cost: float,
    rules: list[PricingRule],
    tax_rate: float,
) -> float:
    """复刻 calculateFinalItemUnitCost：净价套规则后，按税率上浮（净价或税率为 0 则跳过）。"""
    net_cost = apply_pricing_rules(base_cost, rules)
    if net_cost == 0 or tax_rate == 0:
        return net_cost
    return net_cost * (1 + tax_rate / 100)


def resolve_tax_rate(item_tax_rate: float | None) -> float:
    """物料 Item Tax 缺失（None）时兜底 VAT 7%；否则用给定税率（含 0）。"""
    return DEFAULT_TAX_RATE if item_tax_rate is None else item_tax_rate


def final_unit_cost_with_rule(
    base_cost: float,
    tax_rate: float | None,
    rule: PricingRule,
    price_list: str | None,
) -> float:
    """新入口（条件套 margin）：仅 rule_applies 时套 margin，再过税率。

    对齐 ttpos appliesToItemUnitCost + calculateFinalItemUnitCost 组合语义：
      价表不匹配 / 规则停用 → 不套 margin，只上浮税。
    真源: ttpos-bmp/app/ttpos-erp/internal/logic/stock/item.go:279-296
    """
    active_rules: list[PricingRule] = (
        [PricingRule(rule.margin_type, rule.margin_rate_or_amount)]
        if rule_applies(rule, price_list)
        else []
    )
    return calculate_final_item_unit_cost(base_cost, active_rules, resolve_tax_rate(tax_rate))


def final_unit_cost(
    base_cost: float,
    tax_rate: float | None = None,
    rules: list[PricingRule] | None = None,
) -> float:
    """
    [已废弃] 便捷入口：无条件套 5% margin，用于 v4 基准对账。

    新代码请用 final_unit_cost_with_rule，可按 Pricing Rule 条件判定是否套 margin。

    - rules 默认 = ERPNext buying 默认加价规则（Percentage 5%），无条件套用。
    - tax_rate=None → 兜底 7%；显式传 0 → 不上浮税。
    """
    warnings.warn(
        "final_unit_cost 无条件套 5% margin，已废弃；"
        "新代码请用 final_unit_cost_with_rule 按条件判定是否套 margin。",
        DeprecationWarning,
        stacklevel=2,
    )
    rs = rules if rules is not None else [PricingRule(*DEFAULT_BUYING_RULE)]
    tr = resolve_tax_rate(tax_rate)
    return calculate_final_item_unit_cost(base_cost, rs, tr)


def enrich_bom_prices(
    rows: list[dict],
    *,
    base_key: str = "基价(原始)",
    tax_key: str = "适用税率%",
    out_key: str = "ERPNext新单价",
    rules: list[PricingRule] | None = None,
) -> list[dict]:
    """
    给已带 (基价, 税率) 的 BOM 行补算 ERPNext 终价列。
    上游负责把 ERPNext Item Price + UOM 换算 + 税率 落到 base_key/tax_key，
    本函数只做纯算法，不触网，便于离线复跑与对账。
    """
    out = []
    for r in rows:
        base = r.get(base_key)
        if base is None:
            out.append(r)
            continue
        tax = r.get(tax_key)
        r = dict(r)
        r[out_key] = final_unit_cost(float(base), None if tax is None else float(tax), rules)
        out.append(r)
    return out
