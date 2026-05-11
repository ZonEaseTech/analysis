"""Month → Bangkok-time timestamp window.

Anchors the +07:00 convention. A bug here makes EVERY report off by a day
at the month boundary, and is essentially invisible until reconciliation
fails. So we lock month/year boundaries explicitly.
"""
import unittest
from datetime import datetime, timedelta, timezone

from tests import _setup  # noqa: F401

from bq_reports.profit_margin_report import BKK_TZ, _month_to_ts_range


BKK = timezone(timedelta(hours=7))


def bkk(y, m, d=1) -> int:
    return int(datetime(y, m, d, 0, 0, 0, tzinfo=BKK).timestamp())


class MonthToTsRange(unittest.TestCase):
    def test_bkk_tz_is_plus_seven(self):
        # Anchor the timezone offset itself — if anyone "fixes" it to UTC
        # the entire ttpos contract breaks.
        self.assertEqual(BKK_TZ.utcoffset(None), timedelta(hours=7))

    def test_march_2026(self):
        start, end = _month_to_ts_range("2026-03")
        self.assertEqual(start, bkk(2026, 3, 1))
        self.assertEqual(end, bkk(2026, 4, 1))      # end-exclusive
        # Sanity: 31-day month
        self.assertEqual(end - start, 31 * 86400)

    def test_january_rolls_into_february(self):
        start, end = _month_to_ts_range("2026-01")
        self.assertEqual(start, bkk(2026, 1, 1))
        self.assertEqual(end, bkk(2026, 2, 1))
        self.assertEqual(end - start, 31 * 86400)

    def test_december_rolls_into_next_year(self):
        """The 'mon == 12' branch — easy to break with off-by-one math."""
        start, end = _month_to_ts_range("2026-12")
        self.assertEqual(start, bkk(2026, 12, 1))
        self.assertEqual(end, bkk(2027, 1, 1))
        self.assertEqual(end - start, 31 * 86400)

    def test_non_leap_february(self):
        start, end = _month_to_ts_range("2026-02")
        self.assertEqual(end - start, 28 * 86400)

    def test_leap_february(self):
        """2024 is a leap year — Feb has 29 days."""
        start, end = _month_to_ts_range("2024-02")
        self.assertEqual(end - start, 29 * 86400)

    def test_century_non_leap(self):
        """1900 was NOT a leap year (divisible by 100, not 400)."""
        start, end = _month_to_ts_range("1900-02")
        self.assertEqual(end - start, 28 * 86400)

    def test_boundary_is_midnight_bkk(self):
        """The boundary instant must be exactly 00:00:00 +07:00, not 00:00 UTC.
        Anchors the timezone semantic at the cell level."""
        start, _ = _month_to_ts_range("2026-03")
        # 1 March 2026 00:00 +07:00 == 28 Feb 2026 17:00 UTC
        as_utc = datetime.fromtimestamp(start, tz=timezone.utc)
        self.assertEqual(as_utc.day, 28)
        self.assertEqual(as_utc.month, 2)
        self.assertEqual(as_utc.hour, 17)


if __name__ == "__main__":
    unittest.main()
