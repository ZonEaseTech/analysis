"""KPI Ratios — 财务 + 餐饮行业标准比率库 (P3.5).

设计目的: 把绝对数字 (营业额 3,623 万) 转成可读比率 (Gross Margin 65%) +
跟行业基准对比, 老板/财务一眼看健不健康.

行业基准来自 docs/pnl-primer-for-engineers.md 第 4 节 (餐饮行业经验值, NRA 等):

  Gross Margin %      60-70%
  Food Cost %         28-35%
  Labor Cost %        25-30%
  Prime Cost %        55-65% (餐饮核心运营指标)
  Operating Margin %  8-15%
  Net Margin %        3-9%

调整基准 = 改 INDUSTRY_BENCHMARKS 一处, 所有报表自动同步.

不在范围内:
  - SSS 同店增长 (需历史多期数据, P5 范围)
  - Daypart 时段拆分 (单独课题, 不在 P3.5)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class HealthStatus(str, Enum):
    """比率健康度等级 — 对应 Excel 颜色规则。"""

    HEALTHY = "healthy"           # 绿: 健康区间
    ACCEPTABLE = "acceptable"     # 无色: 偏离但可接受
    WARNING = "warning"           # 黄: 偏离较多, 需关注
    CRITICAL = "critical"         # 红: 远离健康区间, 必查
    NOT_AVAILABLE = "n/a"         # 灰: 数据缺失


@dataclass(frozen=True)
class Benchmark:
    """行业基准 — 用于自动评级。"""

    healthy_low: float            # 健康下限
    healthy_high: float           # 健康上限
    direction: str                # "higher_better" (毛利率) | "lower_better" (成本率)
    description: str              # 给 Excel comment


# 餐饮行业经验基准 (docs/pnl-primer-for-engineers.md 第 4 节)
INDUSTRY_BENCHMARKS: dict[str, Benchmark] = {
    "gross_margin": Benchmark(
        0.60, 0.70, "higher_better",
        "餐饮行业 Gross Margin 健康区间 60-70%",
    ),
    "food_cost": Benchmark(
        0.28, 0.35, "lower_better",
        "Food Cost % 健康 28-35% (COGS / Net Sales)",
    ),
    "labor_cost": Benchmark(
        0.25, 0.30, "lower_better",
        "Labor Cost % 健康 25-30% (人力 / Net Sales)",
    ),
    "prime_cost": Benchmark(
        0.55, 0.65, "lower_better",
        "Prime Cost % 健康 55-65% (食材+人力)/Net Sales — 餐饮核心指标",
    ),
    "operating_margin": Benchmark(
        0.08, 0.15, "higher_better",
        "Operating Margin % 健康 8-15%",
    ),
    "net_margin": Benchmark(
        0.03, 0.09, "higher_better",
        "Net Margin % 健康 3-9%",
    ),
    "effective_take_rate": Benchmark(
        0.20, 0.30, "lower_better",
        "Effective Take Rate 行业 20-30%",
    ),
}


@dataclass(frozen=True)
class Kpi:
    """一个 KPI 指标 — 值 + 评级 + 基准。"""

    code: str
    name_zh: str
    name_en: str
    value: Optional[float]        # None = N/A
    format: str                   # "percent" | "number" | "currency"
    benchmark: Optional[Benchmark] = None
    health: HealthStatus = HealthStatus.NOT_AVAILABLE
    note: str = ""


# ───────────────────────────────────────────────────────────────
# 主入口
# ───────────────────────────────────────────────────────────────

def compute_kpis(
    *,
    pnl_amounts: dict[str, float],
    sales_totals: dict[str, float],
    labor_cost: Optional[float] = None,
) -> list[Kpi]:
    """从 P&L 金额 + 销售汇总 (+ 可选人力成本) 算所有 KPI。

    Args:
        pnl_amounts: PnlStatement.all_amounts() 输出
            需要: net_sales, gross_profit (cogs/platform_commission 可选)
        sales_totals: aggregate_sales 输出
            需要: qty, order_count, dine_qty, takeout_qty,
                 dine_sales_price, takeout_sales_price
        labor_cost: 人力成本 (Phase 3 接入后); None 时 Labor/Prime Cost 标 N/A

    Returns:
        Kpi list, 按显示顺序排好.
    """
    kpis: list[Kpi] = []
    net_sales = float(pnl_amounts.get("net_sales", 0.0))
    cogs = abs(float(pnl_amounts.get("cogs", 0.0)))   # cogs 在 PnlLayer 里是负值
    gross_profit = float(pnl_amounts.get("gross_profit", 0.0))
    operating_income = float(pnl_amounts.get("operating_income", 0.0))
    platform_commission = abs(float(pnl_amounts.get("platform_commission", 0.0)))

    # ─── 利润率类 ───────────────────────────────────

    kpis.append(_ratio_kpi(
        "gross_margin", "Gross Margin (毛利率)", "Gross Margin %",
        _safe_div(gross_profit, net_sales),
        "gross_margin",
    ))

    kpis.append(_ratio_kpi(
        "food_cost", "Food Cost % (食材成本率)", "Food Cost %",
        _safe_div(cogs, net_sales),
        "food_cost",
        note="" if cogs > 0 else "COGS 缺失, N/A",
    ))

    # Labor Cost % — Phase 3 数据
    labor_pct = _safe_div(labor_cost, net_sales) if labor_cost is not None else None
    kpis.append(_ratio_kpi(
        "labor_cost", "Labor Cost % (人力成本率)", "Labor Cost %",
        labor_pct, "labor_cost",
        note="待 Phase 3 接入人力数据" if labor_cost is None else "",
    ))

    # Prime Cost % — Food + Labor; 餐饮核心
    if labor_cost is not None and cogs > 0:
        prime_pct = _safe_div(cogs + labor_cost, net_sales)
    else:
        prime_pct = None
    kpis.append(_ratio_kpi(
        "prime_cost", "Prime Cost % (餐饮核心)", "Prime Cost %",
        prime_pct, "prime_cost",
        note="待 Phase 3 接入人力" if labor_cost is None else "",
    ))

    # Operating Margin % — 等 Phase 3 固定成本
    op_pct = _safe_div(operating_income, net_sales) if operating_income else None
    kpis.append(_ratio_kpi(
        "operating_margin", "Operating Margin (经营利润率)", "Operating Margin %",
        op_pct, "operating_margin",
        note="待 Phase 3 接入固定成本" if not operating_income else "",
    ))

    # ─── AOV (客单价) ─────────────────────────────────

    order_count = float(sales_totals.get("order_count", 0))
    if order_count > 0:
        kpis.append(Kpi(
            code="aov", name_zh="客单价 (AOV)", name_en="Average Order Value",
            value=net_sales / order_count, format="number",
        ))
    else:
        kpis.append(Kpi(
            code="aov", name_zh="客单价 (AOV)", name_en="Average Order Value",
            value=None, format="number",
            note="order_count 字段未在 SQL 输出, 待补",
        ))

    # ─── Channel Mix (渠道占比) ───────────────────────

    dine_sales = float(sales_totals.get("dine_sales_price", 0.0))
    takeout_sales = float(sales_totals.get("takeout_sales_price", 0.0))
    total_sales = dine_sales + takeout_sales
    if total_sales > 0:
        kpis.append(Kpi(
            code="dine_mix", name_zh="堂食占比", name_en="Dine-in Mix %",
            value=dine_sales / total_sales, format="percent",
        ))
        kpis.append(Kpi(
            code="takeout_mix", name_zh="外卖占比", name_en="Takeout Mix %",
            value=takeout_sales / total_sales, format="percent",
        ))

    # ─── Effective Take Rate (估算) ───────────────────

    if platform_commission > 0 and takeout_sales > 0:
        kpis.append(_ratio_kpi(
            "effective_take_rate",
            "Effective Take Rate (估算)", "Effective Take Rate %",
            platform_commission / takeout_sales,
            "effective_take_rate",
            note="估算值, 待 Phase 2 接平台对账单升真值",
        ))

    return kpis


# ───────────────────────────────────────────────────────────────
# 内部辅助
# ───────────────────────────────────────────────────────────────

def _safe_div(num: Optional[float], denom: float) -> Optional[float]:
    """除法保护; num 为 None 或分母 0 返回 None。"""
    if num is None or not denom or denom == 0:
        return None
    return num / denom


def _ratio_kpi(
    code: str,
    name_zh: str,
    name_en: str,
    value: Optional[float],
    benchmark_key: str,
    note: str = "",
) -> Kpi:
    """构造一个比率类 KPI, 自动套用行业基准评级。"""
    bench = INDUSTRY_BENCHMARKS.get(benchmark_key)
    health = _classify_health(value, bench) if bench else HealthStatus.NOT_AVAILABLE
    return Kpi(
        code=code, name_zh=name_zh, name_en=name_en,
        value=value, format="percent",
        benchmark=bench, health=health, note=note,
    )


def _classify_health(value: Optional[float], bench: Benchmark) -> HealthStatus:
    """按基准评健康度. higher_better / lower_better 反向."""
    if value is None:
        return HealthStatus.NOT_AVAILABLE

    low, high = bench.healthy_low, bench.healthy_high
    if bench.direction == "higher_better":
        if value < low - 0.10:
            return HealthStatus.CRITICAL
        if value < low:
            return HealthStatus.WARNING
        if value <= high:
            return HealthStatus.HEALTHY
        return HealthStatus.ACCEPTABLE
    else:  # lower_better
        if value > high + 0.10:
            return HealthStatus.CRITICAL
        if value > high:
            return HealthStatus.WARNING
        if value >= low:
            return HealthStatus.HEALTHY
        return HealthStatus.ACCEPTABLE
