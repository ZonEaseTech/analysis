"""弧酷 (前任 POS) — 2026-01 切换前历史销售数据接入.

只用于 1 月报表的"22 个 1 月底才切到 ttpos 的店"补集.
2 月+ 主报表 BQ 数据完整, 不需要这块.
"""
from external_sales.huku.loader import HukuLoader

__all__ = ["HukuLoader"]
