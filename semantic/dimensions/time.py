"""Time-window helpers.

Owns the **Bangkok timezone (+07:00)** convention that aligns every ttpos
report's month boundary with the business day used by the POS. Don't change
the offset without coordinating with the shop schedule.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


BKK_TZ = timezone(timedelta(hours=7))


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
