"""P&L Statement layer aggregator — 财务损益表分层聚合 (P3.5).

把 sale_event / sale_line / takeout_line 销售域字段按**财务标准损益表结构**
映射成 PnlLayer 列表。报表层调用 → 得到 PnlStatement → 直接喂 Excel writer.

业界标准 P&L 5 层 (参考 docs/pnl-primer-for-engineers.md 第 1 节):

  GMV / Gross Revenue              总营业额
  − Returns & Allowances            退款 + 取消
  − Promotional Deductions          赠品 + 赠送 + 调价折扣
  = Net Sales / Net Revenue         净销售额  (= ttpos actual_sale_amount)
  − COGS                            物料成本
  = Gross Profit                    销售毛利           ← 我们做到这层
  − Variable Selling Costs          平台抽佣 / 配送费分担 / 支付通道费
  = Contribution Margin             贡献毛利 (估算)
  − Fixed OpEx                      房租 / 人力 / 水电 / 营销
  = Operating Income (EBIT)         经营利润

数据可信度分级 (Confidence):
  ACTUAL        — 源系统真实字段, 已对账 (Phase 1 + ttpos 对账锚)
  DERIVED       — 由真实字段计算 (Net Sales = GMV − 损失项)
  ESTIMATED     — 估算值 (按行业抽佣率算的平台抽佣 — Phase 2 接对账单后升 ACTUAL)
  NOT_AVAILABLE — 数据源未接入, 报表标 N/A

跟 P1 Resolver 的衔接:
  commission_rate_resolver 通过 P3 fact_overrides 加载 (resolvers.yaml),
  build_pnl() 调 resolver.resolve(platform) 拿抽佣率, 把 Resolved.source 写进
  PnlLayer.formula 让审计 sheet 显示真实来源.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Optional


class Confidence(str, Enum):
    """数据可信度等级 — 报表里 N/A 显式标注必备。"""

    ACTUAL = "actual"
    DERIVED = "derived"
    ESTIMATED = "estimated"
    NOT_AVAILABLE = "n/a"


@dataclass(frozen=True)
class PnlLayer:
    """P&L 一行 — 金额 + 元数据 + confidence."""

    code: str                          # 程序化 key (稳定不变), e.g. "gmv" / "net_sales"
    name_zh: str                       # 显示名 (中文)
    name_en: str                       # 显示名 (英文, 财务对照用)
    amount: float                      # 金额 (单位 THB 元; writer 决定要不要除以 1000)
    confidence: Confidence             # 可信度
    source_table: str = ""             # BQ 来源表
    source_cte: str = ""               # semantic 层 CTE 名
    formula: str = ""                  # 计算公式 (人类可读)
    is_subtotal: bool = False          # 是否关键节点 (Net Sales / Gross Profit 等, 加粗)
    indent: int = 0                    # 缩进层级 (0=节点, 1=明细减项, 2=子明细)
    note: str = ""                     # 备注 ("集团促销" / "待接入" 等)


@dataclass
class PnlStatement:
    """整张 P&L 损益表 — 按顺序的 PnlLayer 列表 + 元数据。"""

    period: str                        # 期间标识 ("2026-04")
    scope: str                         # 范围 ("全集团" / store_num)
    layers: list[PnlLayer] = field(default_factory=list)

    def by_code(self, code: str) -> Optional[PnlLayer]:
        for layer in self.layers:
            if layer.code == code:
                return layer
        return None

    def subtotal_amounts(self) -> dict[str, float]:
        """所有 subtotal 节点的 {code: amount}, 给比率计算用。"""
        return {layer.code: layer.amount for layer in self.layers if layer.is_subtotal}

    def all_amounts(self) -> dict[str, float]:
        """所有 layer 的 {code: amount}。"""
        return {layer.code: layer.amount for layer in self.layers}


# ───────────────────────────────────────────────────────────────
# 标准 P&L 行序骨架
# 改这个表 = 改 P&L 结构本身, 等同改财报模板, 需要 review.
# ───────────────────────────────────────────────────────────────

# (code, name_zh, name_en, is_subtotal, indent)
_STANDARD_LAYERS = [
    # 第 1 层: GMV → Net Sales
    ("gmv",                  "GMV / 总营业额",          "GMV / Gross Revenue",    True,  0),
    ("dine_gmv",             "  堂食营业额",            "  Dine-in Revenue",      False, 1),
    ("takeout_gmv",          "  外卖营业额",            "  Takeout Revenue",      False, 1),
    ("returns_allowances",   "  减 退款金额",           "  Less: Returns",        False, 1),
    ("cancellations",        "  减 外卖取消",           "  Less: Cancellations",  False, 1),
    ("promo_deductions",     "  减 促销让利合计",       "  Less: Promotions",     False, 1),
    ("free_amount",          "    赠品金额",            "    Free items",         False, 2),
    ("give_amount",          "    赠送金额",            "    Comp / Give",        False, 2),
    ("discount_amount",      "    调价折扣",            "    Discounts",          False, 2),
    ("net_sales",            "净销售额 (Net Sales)",    "Net Sales",              True,  0),
    # 第 2 层: → Gross Profit
    ("cogs",                 "  减 物料成本 (COGS)",    "  Less: COGS",           False, 1),
    ("dine_cogs",            "    堂食物料成本",        "    Dine-in COGS",       False, 2),
    ("takeout_cogs",         "    外卖物料成本",        "    Takeout COGS",       False, 2),
    ("gross_profit",         "销售毛利 (Gross Profit)", "Gross Profit",           True,  0),
    # 第 3 层: → Contribution Margin (估算/N/A)
    ("platform_commission",  "  减 平台抽佣",           "  Less: Platform Take",  False, 1),
    ("delivery_fee_share",   "  减 配送费分担",         "  Less: Delivery Fee",   False, 1),
    ("payment_processing",   "  减 支付通道费",         "  Less: Payment Fees",   False, 1),
    ("contribution_margin",  "贡献毛利 (Contribution)", "Contribution Margin",    True,  0),
    # 第 4 层: → Operating Income (N/A)
    ("rent",                 "  减 房租",               "  Less: Rent",           False, 1),
    ("labor",                "  减 人力",               "  Less: Labor",          False, 1),
    ("utilities",            "  减 水电气",             "  Less: Utilities",      False, 1),
    ("marketing",            "  减 营销",               "  Less: Marketing",      False, 1),
    ("operating_income",     "经营利润 (Operating)",    "Operating Income",       True,  0),
]


# ───────────────────────────────────────────────────────────────
# 销售域聚合 (从 sale_event-like rows)
# ───────────────────────────────────────────────────────────────

_SALES_METRIC_KEYS = [
    "sales_price", "dine_sales_price", "takeout_sales_price",
    "actual_amount", "refund_amount", "cancelled_amount",
    "free_amount", "give_amount", "discount_amount",
    "qty", "dine_qty", "takeout_qty", "order_count",
]


def aggregate_sales(rows: Iterable[Any]) -> dict[str, float]:
    """SUM 所有指标返回 dict。Missing 字段当 0。

    rows 元素支持 attr 访问 (BQ Row / SimpleNamespace) 或 dict。
    报表入口需要保证 row 上有 dine_sales_price / takeout_sales_price 字段
    (实施时 SQL 在 merged CTE 多输出 dine/takeout 拆列, 跟 path 3 一致).
    """
    totals = {k: 0.0 for k in _SALES_METRIC_KEYS}
    for row in rows:
        for k in _SALES_METRIC_KEYS:
            val = _read(row, k, default=0)
            try:
                totals[k] += float(val or 0)
            except (TypeError, ValueError):
                pass
    return totals


def _read(row: Any, key: str, default: Any = None) -> Any:
    """统一访问器: BQ Row / SimpleNamespace / dict 都支持。"""
    if hasattr(row, key):
        return getattr(row, key)
    if isinstance(row, dict):
        return row.get(key, default)
    return default


# ───────────────────────────────────────────────────────────────
# 主入口
# ───────────────────────────────────────────────────────────────

# 估算抽佣的默认平台分布权重 (跟 toi.platform 字段一致)
_DEFAULT_PLATFORMS = ("grab", "lineman", "shopee")


def build_pnl(
    *,
    period: str,
    scope: str,
    sales_rows: Iterable[Any],
    cogs_data: Optional[dict] = None,
    commission_rate_resolver: Any = None,
    settlement_rows: Optional[Iterable[Any]] = None,
    fixed_costs: Optional[dict[str, float]] = None,
) -> PnlStatement:
    """从原始数据组装 P&L Statement.

    Args:
        period: 期间标识 ("2026-04")
        scope: 范围标识 ("全集团" / store_num)
        sales_rows: 销售事实行. 每行需要的字段 (缺则当 0):
            sales_price, dine_sales_price, takeout_sales_price,
            actual_amount, refund_amount, cancelled_amount,
            free_amount, give_amount, discount_amount,
            qty, dine_qty, takeout_qty, order_count
        cogs_data: {'total': float, 'dine': float, 'takeout': float}
            None 时物料成本层标 NOT_AVAILABLE
        commission_rate_resolver: Resolver (P3) 或 None.
            None → platform_commission 层标 NOT_AVAILABLE
            提供时 → 按 takeout_gmv × resolver.resolve('default'/avg) 估算
        settlement_rows: Phase 2 真实平台对账行 (暂未实现, 保留接口)
        fixed_costs: Phase 3 固定成本 dict (暂未实现, 保留接口)

    Returns:
        PnlStatement 含全部 PnlLayer (含 NOT_AVAILABLE 占位)
    """
    sales_totals = aggregate_sales(sales_rows)

    # 第 1 层金额
    gmv = sales_totals["sales_price"]
    dine_gmv = sales_totals["dine_sales_price"]
    takeout_gmv = sales_totals["takeout_sales_price"]
    returns_allowances = sales_totals["refund_amount"]
    cancellations = sales_totals["cancelled_amount"]
    free_amt = sales_totals["free_amount"]
    give_amt = sales_totals["give_amount"]
    discount_amt = sales_totals["discount_amount"]
    promo_deductions = free_amt + give_amt + discount_amt

    # Net Sales = ttpos actual_sale_amount (这就是对账锚)
    net_sales = sales_totals["actual_amount"]

    # 第 2 层 COGS
    if cogs_data is not None:
        cogs_total = float(cogs_data.get("total", 0.0))
        dine_cogs = float(cogs_data.get("dine", 0.0))
        takeout_cogs = float(cogs_data.get("takeout", 0.0))
        gross_profit = net_sales - cogs_total
        cogs_conf = Confidence.ACTUAL
    else:
        cogs_total = dine_cogs = takeout_cogs = 0.0
        gross_profit = 0.0
        cogs_conf = Confidence.NOT_AVAILABLE

    # 第 3 层估算 platform_commission
    commission_source = ""
    if commission_rate_resolver is not None and takeout_gmv > 0:
        # 简化: 用 "default" key 拿一个综合抽佣率
        # 后续可扩展: 按平台拆分 takeout_gmv × 各平台 rate
        commission_result = commission_rate_resolver.resolve("default")
        if commission_result is None:
            # fallback: 行业经验值
            rate = 0.25
            commission_source = "industry_default_25pct"
        else:
            rate = float(commission_result.value)
            commission_source = commission_result.source
        platform_commission = takeout_gmv * rate
        contribution_margin = gross_profit - platform_commission
        commission_conf = Confidence.ESTIMATED
    else:
        platform_commission = 0.0
        contribution_margin = 0.0
        commission_conf = Confidence.NOT_AVAILABLE

    # 第 4 层 (全部 N/A)
    rent = labor = utilities = marketing = 0.0
    operating_income = 0.0

    # 组装 layers
    amounts = {
        "gmv":                 gmv,
        "dine_gmv":            dine_gmv,
        "takeout_gmv":         takeout_gmv,
        "returns_allowances":  -returns_allowances,
        "cancellations":       -cancellations,
        "promo_deductions":    -promo_deductions,
        "free_amount":         -free_amt,
        "give_amount":         -give_amt,
        "discount_amount":     -discount_amt,
        "net_sales":           net_sales,
        "cogs":                -cogs_total,
        "dine_cogs":           -dine_cogs,
        "takeout_cogs":        -takeout_cogs,
        "gross_profit":        gross_profit,
        "platform_commission": -platform_commission,
        "delivery_fee_share":  0.0,
        "payment_processing":  0.0,
        "contribution_margin": contribution_margin,
        "rent":                -rent,
        "labor":               -labor,
        "utilities":           -utilities,
        "marketing":           -marketing,
        "operating_income":    operating_income,
    }

    confidences = {
        "gmv":                 Confidence.ACTUAL,
        "dine_gmv":            Confidence.ACTUAL,
        "takeout_gmv":         Confidence.ACTUAL,
        "returns_allowances":  Confidence.ACTUAL,
        "cancellations":       Confidence.ACTUAL,
        "promo_deductions":    Confidence.DERIVED,
        "free_amount":         Confidence.ACTUAL,
        "give_amount":         Confidence.ACTUAL,
        "discount_amount":     Confidence.ACTUAL,
        "net_sales":           Confidence.DERIVED,
        "cogs":                cogs_conf,
        "dine_cogs":           cogs_conf,
        "takeout_cogs":        cogs_conf,
        "gross_profit":        cogs_conf,
        "platform_commission": commission_conf,
        "delivery_fee_share":  Confidence.NOT_AVAILABLE,
        "payment_processing":  Confidence.NOT_AVAILABLE,
        "contribution_margin": commission_conf,
        "rent":                Confidence.NOT_AVAILABLE,
        "labor":               Confidence.NOT_AVAILABLE,
        "utilities":           Confidence.NOT_AVAILABLE,
        "marketing":           Confidence.NOT_AVAILABLE,
        "operating_income":    Confidence.NOT_AVAILABLE,
    }

    sources = {
        "gmv": ("ttpos_statistics_product + ttpos_takeout_order_item",
                "merged",
                "shop_sales.sales_price + takeout_sales.sales_price"),
        "dine_gmv": ("ttpos_statistics_product", "shop_sales",
                     "SUM(product_sale_price * product_num)"),
        "takeout_gmv": ("ttpos_takeout_order_item", "takeout_sales",
                        "SUM(IF state IN active, price * quantity, 0)"),
        "returns_allowances": ("ttpos_statistics_product", "shop_sales",
                               "SUM(product_sale_price * refund_num)"),
        "cancellations": ("ttpos_takeout_order_item", "takeout_sales",
                          "SUM(IF state = 60, price * quantity, 0)"),
        "free_amount": ("ttpos_statistics_product", "shop_sales",
                        "SUM(IF free_num > 0, sale_price * num, 0)"),
        "give_amount": ("ttpos_statistics_product", "shop_sales",
                        "SUM(IF give_num > 0, sale_price * num, 0)"),
        "discount_amount": ("ttpos_statistics_product", "shop_sales",
                            "SUM((sale_price - final_price) * (num - refund_num))"),
        "net_sales": ("derived", "—",
                      "= GMV − Returns − Cancellations − Promotions "
                      "= ttpos actual_sale_amount"),
        "cogs": ("ttpos_product_bom + qty", "bom",
                 "Σ bom_num × unit_price × qty"),
        "gross_profit": ("derived", "—", "= Net Sales − COGS"),
        "platform_commission": (
            "estimated",
            "—",
            f"= takeout_gmv × commission_rate [source={commission_source}] (Phase 2 改真值)"
            if commission_rate_resolver is not None else "待接入",
        ),
        "contribution_margin": (
            "derived (含估算)", "—",
            "= Gross Profit − Variable Costs"
            if commission_rate_resolver is not None else "待接入",
        ),
    }

    layers = []
    for code, name_zh, name_en, is_sub, indent in _STANDARD_LAYERS:
        src = sources.get(code, ("", "", ""))
        layers.append(PnlLayer(
            code=code,
            name_zh=name_zh,
            name_en=name_en,
            amount=amounts.get(code, 0.0),
            confidence=confidences.get(code, Confidence.NOT_AVAILABLE),
            source_table=src[0],
            source_cte=src[1],
            formula=src[2],
            is_subtotal=is_sub,
            indent=indent,
        ))

    return PnlStatement(period=period, scope=scope, layers=layers)
