"""`aggregate_by_grain` — generic GROUP BY + SUM.

This is the single piece of code that lets every new "by-X" report skip
writing custom aggregation. If it has a bug, every future report inherits it.
So we test:
  - dimension fidelity (no silent merging across different grain values)
  - metric arithmetic (sum is sum, not avg/last)
  - schema drift tolerance (missing fields = 0, non-numeric = skip)
  - mixed input shapes (dict / object / BQ-like)
"""
import unittest
from types import SimpleNamespace

from tests import _setup  # noqa: F401

from semantic.aggregations.by_grain import aggregate_by_grain


def ev(**kw):
    return SimpleNamespace(**kw)


class BasicGroupAndSum(unittest.TestCase):
    def test_groups_by_single_key(self):
        rows = [
            ev(item="A", qty=5),
            ev(item="A", qty=3),
            ev(item="B", qty=7),
        ]
        out = aggregate_by_grain(rows, ["item"], ["qty"])
        self.assertEqual(out, {("A",): {"qty": 8.0}, ("B",): {"qty": 7.0}})

    def test_groups_by_multi_key_order_preserved(self):
        """Result key tuple must match grain_keys order."""
        rows = [
            ev(item="A", price=10, qty=1),
            ev(item="A", price=12, qty=2),
            ev(item="A", price=10, qty=4),
        ]
        out = aggregate_by_grain(rows, ["item", "price"], ["qty"])
        self.assertIn(("A", 10), out)
        self.assertIn(("A", 12), out)
        self.assertEqual(out[("A", 10)]["qty"], 5.0)
        self.assertEqual(out[("A", 12)]["qty"], 2.0)

    def test_multiple_metrics_summed_independently(self):
        rows = [
            ev(item="X", qty=2, revenue=20.0),
            ev(item="X", qty=3, revenue=45.0),
        ]
        out = aggregate_by_grain(rows, ["item"], ["qty", "revenue"])
        self.assertEqual(out[("X",)], {"qty": 5.0, "revenue": 65.0})

    def test_empty_rows_returns_empty_dict(self):
        self.assertEqual(aggregate_by_grain([], ["item"], ["qty"]), {})


class GrainSensitivity(unittest.TestCase):
    """Same data, different grain → different groupings. The same event row
    set must roll up correctly into:
      - "by item" (collapses price)
      - "by (item, price)" (current ask)
      - "by (item, channel)" (future)
    """

    EVENTS = [
        ev(item="A", price=10, channel="dine",    qty=5, revenue=50),
        ev(item="A", price=10, channel="takeout", qty=3, revenue=30),
        ev(item="A", price=12, channel="dine",    qty=2, revenue=24),
        ev(item="B", price=10, channel="dine",    qty=4, revenue=40),
    ]

    def test_by_item(self):
        out = aggregate_by_grain(self.EVENTS, ["item"], ["qty", "revenue"])
        self.assertEqual(out[("A",)], {"qty": 10.0, "revenue": 104.0})
        self.assertEqual(out[("B",)], {"qty": 4.0,  "revenue": 40.0})

    def test_by_item_and_price(self):
        out = aggregate_by_grain(self.EVENTS, ["item", "price"], ["qty", "revenue"])
        self.assertEqual(out[("A", 10)], {"qty": 8.0, "revenue": 80.0})
        self.assertEqual(out[("A", 12)], {"qty": 2.0, "revenue": 24.0})
        self.assertEqual(out[("B", 10)], {"qty": 4.0, "revenue": 40.0})

    def test_by_item_and_channel(self):
        out = aggregate_by_grain(self.EVENTS, ["item", "channel"], ["qty"])
        self.assertEqual(out[("A", "dine")]["qty"], 7.0)      # 5 + 2
        self.assertEqual(out[("A", "takeout")]["qty"], 3.0)

    def test_finer_grain_preserves_total(self):
        """Sanity: regardless of grain, total qty must be identical."""
        total_coarse = sum(b["qty"] for b in
                           aggregate_by_grain(self.EVENTS, ["item"], ["qty"]).values())
        total_fine = sum(b["qty"] for b in
                         aggregate_by_grain(self.EVENTS, ["item", "price", "channel"],
                                            ["qty"]).values())
        self.assertEqual(total_coarse, total_fine)


class SchemaDriftTolerance(unittest.TestCase):
    def test_missing_metric_treated_as_zero(self):
        rows = [
            ev(item="A", qty=5),                        # no 'revenue'
            ev(item="A", qty=3, revenue=30),
        ]
        out = aggregate_by_grain(rows, ["item"], ["qty", "revenue"])
        self.assertEqual(out[("A",)], {"qty": 8.0, "revenue": 30.0})

    def test_none_metric_treated_as_zero(self):
        rows = [ev(item="A", qty=None), ev(item="A", qty=5)]
        out = aggregate_by_grain(rows, ["item"], ["qty"])
        self.assertEqual(out[("A",)]["qty"], 5.0)

    def test_non_numeric_metric_skipped(self):
        rows = [
            ev(item="A", qty="not_a_number"),
            ev(item="A", qty=4),
        ]
        out = aggregate_by_grain(rows, ["item"], ["qty"])
        self.assertEqual(out[("A",)]["qty"], 4.0)


class AcceptsMultipleRowShapes(unittest.TestCase):
    """getattr OR dict access — pick whichever consumer is easier."""

    def test_dict_rows(self):
        rows = [{"item": "A", "qty": 5}, {"item": "A", "qty": 3}]
        out = aggregate_by_grain(rows, ["item"], ["qty"])
        self.assertEqual(out[("A",)]["qty"], 8.0)

    def test_attr_rows(self):
        out = aggregate_by_grain([ev(item="A", qty=10)], ["item"], ["qty"])
        self.assertEqual(out[("A",)]["qty"], 10.0)


class InputValidation(unittest.TestCase):
    def test_empty_grain_raises(self):
        with self.assertRaises(ValueError):
            aggregate_by_grain([ev(item="A", qty=1)], [], ["qty"])

    def test_empty_metrics_raises(self):
        with self.assertRaises(ValueError):
            aggregate_by_grain([ev(item="A", qty=1)], ["item"], [])


if __name__ == "__main__":
    unittest.main()
