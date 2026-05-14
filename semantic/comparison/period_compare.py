"""Period comparison — MoM / YoY 通用对比 (P3.5).

给 PnlStatement / KPI 加上"vs 上月" 或 "vs 去年同期" 列.

财务标准:
  MoM (Month over Month): 今月 / 上月 - 1
  YoY (Year over Year):   今月 / 去年同月 - 1
  pp (percentage point):  60% → 65% 是 +5pp 但只是 +8.3%

PeriodChange 同时持有绝对值 + 百分比变化, 让 writer 决定怎么展示.

不在范围: 真正跑 BQ 拉历史数据. 这一层只做"已有两期数据" → 计算变化. 数据获取
由调用方负责 (报表入口去 BQ 跑两次, 拿到两个 PnlStatement, 然后调本模块).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PeriodChange:
    """两期对比的变化量 + 变化率.

    abs_delta = current − previous
    pct_delta = (current − previous) / |previous|

    previous = 0 时 pct_delta = None (避免除零, "无穷大" 没意义).
    current = previous = 0 时 abs_delta = 0, pct_delta = None.

    pp_delta 仅对"已经是百分比"的指标有意义 (Gross Margin 等):
      60% → 65% : abs_delta = 0.05 (= 5pp), pct_delta = 0.0833 (+8.3%)
    """

    current: float
    previous: Optional[float]      # None = 没有上一期数据
    abs_delta: Optional[float]     # current - previous; previous=None 时也 None
    pct_delta: Optional[float]     # (cur - prev) / |prev|; 分母 0 时 None

    @property
    def pp_delta(self) -> Optional[float]:
        """当 current / previous 本身就是百分比时, pp 差 = abs_delta."""
        return self.abs_delta

    @property
    def is_improvement(self) -> Optional[bool]:
        """abs_delta > 0 表示上升 (好坏取决于指标方向, caller 自判)。"""
        if self.abs_delta is None:
            return None
        return self.abs_delta > 0


def compute_period_change(current: float, previous: Optional[float]) -> PeriodChange:
    """单个值的两期对比. 处理 None / 0 边界."""
    if previous is None:
        return PeriodChange(
            current=float(current), previous=None,
            abs_delta=None, pct_delta=None,
        )

    abs_delta = float(current) - float(previous)
    if previous == 0:
        # current 也 0 → 无变化; current 非 0 但 prev=0 → 无穷大不展示
        pct_delta = 0.0 if current == 0 else None
    else:
        pct_delta = abs_delta / abs(float(previous))

    return PeriodChange(
        current=float(current),
        previous=float(previous),
        abs_delta=abs_delta,
        pct_delta=pct_delta,
    )


def compute_mom_changes(
    current_amounts: dict[str, float],
    previous_amounts: Optional[dict[str, float]],
) -> dict[str, PeriodChange]:
    """对一组数字 (e.g. P&L amounts) 算 MoM 变化.

    Args:
        current_amounts: 本期金额 dict (e.g. {"gmv": 36_000_000, "net_sales": ...})
        previous_amounts: 上期 dict; None 时所有 PeriodChange.previous=None

    Returns:
        dict[code, PeriodChange]. current_amounts 里所有 key 都有.
    """
    prev = previous_amounts or {}
    out: dict[str, PeriodChange] = {}
    for code, cur in current_amounts.items():
        out[code] = compute_period_change(cur, prev.get(code))
    return out


def compute_yoy_changes(
    current_amounts: dict[str, float],
    year_ago_amounts: Optional[dict[str, float]],
) -> dict[str, PeriodChange]:
    """对一组数字算 YoY 变化. 实现跟 MoM 完全一样, 只是语义不同 — caller 传去年同月数据.

    数据满 12 个月之前 year_ago_amounts 应该传 None, 让 PeriodChange.previous=None,
    writer 标 "N/A (数据未满 12 个月)".
    """
    return compute_mom_changes(current_amounts, year_ago_amounts)


def format_pct_delta(pct: Optional[float], precision: int = 1) -> str:
    """格式化百分比变化 - 财务化展示.

    None       → "N/A"
    0          → "—"
    +0.083     → "+8.3%"
    -0.15      → "-15.0%"
    """
    if pct is None:
        return "N/A"
    if pct == 0:
        return "—"
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct * 100:.{precision}f}%"
