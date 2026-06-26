"""Time-window helpers.

Owns the **Bangkok timezone (+07:00)** convention that aligns every ttpos
report's month boundary with the business day used by the POS. Don't change
the offset without coordinating with the shop schedule.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


BKK_TZ = timezone(timedelta(hours=7))

# 封存线 (spec 决策 2/3): 该月之前的交付物 = 旧浮点口径, 永不重算.
# 只读对账/审计查询不受限 (观察跑用 BQ 查询, 不走报表导出入口).
FROZEN_BEFORE_MONTH = "2026-06"


def assert_month_not_frozen(month: str) -> None:
    """月份封存守卫 — 报表导出入口必调 (YYYY-MM 字符串字典序可直接比较).

    封存月直接 exit 3 (区别于闸门的 exit 2), 提示去 exports/ 找已交付归档.
    """
    if month < FROZEN_BEFORE_MONTH:
        print(f"🧊 {month} 已封存 (封存线 {FROZEN_BEFORE_MONTH}, 旧浮点口径, 永不重算).")
        print("   已交付文件在 exports/ 归档; 如需对账/审计请用只读查询 (scripts/adhoc/).")
        print("   口径说明: CLAUDE.md「历史封存」节 + docs/superpowers/specs/2026-06-12-zero-tolerance-design.md §6.")
        raise SystemExit(3)


def month_to_ts_range(month: str) -> tuple[int, int]:
    """'YYYY-MM' → (start_ts, end_ts) in BKK timezone, end-exclusive.

    The end boundary is the first second of the following month — callers
    SHOULD use `>= start_ts AND < end_ts` (never `<=`) to avoid double-counting
    midnight transactions.
    """
    year, mon = int(month[:4]), int(month[5:7])
    start_dt = datetime(year, mon, 1, tzinfo=BKK_TZ)
    if mon == 12:
        end_dt = datetime(year + 1, 1, 1, tzinfo=BKK_TZ)
    else:
        end_dt = datetime(year, mon + 1, 1, tzinfo=BKK_TZ)
    return int(start_dt.timestamp()), int(end_dt.timestamp())
