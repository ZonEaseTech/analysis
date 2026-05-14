"""Analytics layer — 差异分解 / 期间对比框架 (P5).

P3 / P3.5 是"展示一期数据", P5 是"解释期与期之间为什么变了".

业界标杆: 老板问"为什么这个月毛利掉了 ¥2k", 机器自动给:
  量差   ¥-450    (销量同比 -3.2%)
  价差   ¥+120    (改价提价)
  成本差 ¥-1,680  (食材成本上涨)
  结构差 ¥-93     (高毛利套餐占比下降 1.2pp)
  总差异 ¥-2,103

这是经营分析中台的核心能力, 不只是数字展示, 是"自动解释".
"""
from .variance_decomposition import (
    GrossProfitVariance,
    Variance,
    decompose_gross_profit,
    decompose_revenue,
)

__all__ = [
    "GrossProfitVariance",
    "Variance",
    "decompose_gross_profit",
    "decompose_revenue",
]
