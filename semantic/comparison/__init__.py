"""Cross-period comparison helpers — MoM / YoY 对比层 (P3.5).

跟 aggregations 同级的新子层. aggregations 算"这一期是多少", comparison 算
"这一期 vs 上一期 / 去年同期 变化多少".

财务报表里所有数字旁边都要有 MoM / YoY, 是行业标配
(参考 docs/pnl-primer-for-engineers.md 第 4 节).
"""
from .period_compare import (
    PeriodChange,
    compute_mom_changes,
    compute_period_change,
    compute_yoy_changes,
    format_pct_delta,
)

__all__ = [
    "PeriodChange",
    "compute_mom_changes",
    "compute_period_change",
    "compute_yoy_changes",
    "format_pct_delta",
]
