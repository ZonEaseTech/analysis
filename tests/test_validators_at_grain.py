"""Validators must still work at finer grain (item, price).

The whole point of having `aggregate_by_grain` + `Identity` decoupled is that
the same identity definitions work regardless of grain. This file pins that.

Two scenarios:
  1. (store, item, price) grain — current "by-price" use case.
  2. (store, item, price, channel) grain — future "by-channel" use case.
Identities never see grain keys; they only read the metric columns. So they
should pass/fail the same way.
"""
import unittest
from types import SimpleNamespace

from tests import _setup  # noqa: F401

from semantic.aggregations.by_grain import aggregate_by_grain
from semantic.entities.sale_event import METRIC_COLUMNS
from semantic.validators import check, Severity
from semantic.validators.identities import DEFAULT_IDENTITIES


def ev(**kw):
    """sale_event-shaped row with all the metrics defaulting to 0."""
    defaults = {m: 0 for m in METRIC_COLUMNS}
    defaults.update(store_num="1", store_name="店A",
                    item_uuid="X", item_name="商品X",
                    price=10.0, channel="dine")
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def derive_for_validation(grouped):
    """Match what main() does: add net_qty + alias revenue=actual_amount."""
    out = []
    for k, v in grouped.items():
        net = (v["qty"] - v["free_qty"] - v["give_qty"]
               - v["refund_qty"] - v["cancelled_qty"])
        out.append({"store_num": k[0], "item_name": k[3], "price": k[4],
                    "net_qty": net,
                    "revenue": v["actual_amount"],
                    **v})
    return out


GRAIN = ["store_num", "store_name", "item_uuid", "item_name", "price"]


class IdentitiesAtPriceGrain(unittest.TestCase):

    def test_clean_per_price_row_passes_both_identities(self):
        events = [
            # ¥10 tier: 100 sold cleanly, all収 collected, no losses
            ev(price=10.0, qty=100, sales_price=1000, actual_amount=1000,
               gross_amount=1000),  # = sales_price(1000) + cancelled_amount(0)
            # ¥5 tier: 50 sold, ¥10 of refund (2 returned at ¥5 standard)
            ev(price=5.0, qty=50, sales_price=250, actual_amount=240,
               refund_qty=2, refund_amount=10,
               gross_amount=250),   # = sales_price(250) + cancelled_amount(0)
        ]
        grouped = aggregate_by_grain(events, GRAIN, METRIC_COLUMNS)
        rows = derive_for_validation(grouped)
        result = check(rows, DEFAULT_IDENTITIES)
        self.assertEqual(result.violations, [],
                         f"identities should hold per (item, price); got {result.violations}")

    def test_qty_identity_violation_caught_at_price_grain(self):
        """If we inject a 2-unit净销量 drift on the ¥10 tier, validator must catch it."""
        events = [
            ev(price=10.0, qty=10, free_qty=1, give_qty=0,
               refund_qty=0, cancelled_qty=0,
               sales_price=100, actual_amount=80),
        ]
        grouped = aggregate_by_grain(events, GRAIN, METRIC_COLUMNS)
        rows = derive_for_validation(grouped)
        # Hand-tamper net_qty so it doesn't match
        for r in rows:
            r["net_qty"] = 7   # should be 9 (10 - 1)
        result = check(rows, DEFAULT_IDENTITIES)
        qty_viols = result.by_identity("销量恒等式")
        self.assertEqual(len(qty_viols), 1)
        self.assertEqual(qty_viols[0].severity, Severity.MUST_FIX)

    def test_money_identity_violation_caught_at_price_grain(self):
        """sales_price way off RHS → MUST_FIX."""
        events = [
            ev(price=10.0, qty=50, sales_price=600, actual_amount=200),
            # delta = 600 - (200+0+0+0+0+0) = 400 > 100 → MUST_FIX
        ]
        grouped = aggregate_by_grain(events, GRAIN, METRIC_COLUMNS)
        rows = derive_for_validation(grouped)
        result = check(rows, DEFAULT_IDENTITIES)
        money_viols = result.by_identity("金额恒等式")
        self.assertEqual(len(money_viols), 1)
        self.assertEqual(money_viols[0].severity, Severity.MUST_FIX)


class IdentitiesAtChannelGrain(unittest.TestCase):
    """Same identities, finer grain (store, item, price, channel). Should
    still pass per-row — proves the底座 doesn't need per-grain identity tuning."""

    def test_per_channel_row_passes(self):
        events = [
            ev(price=10.0, channel="dine",    qty=60, sales_price=600, actual_amount=600,
               gross_amount=600),   # = sales_price(600) + cancelled_amount(0)
            ev(price=10.0, channel="takeout", qty=40, sales_price=400, actual_amount=400,
               gross_amount=400),   # = sales_price(400) + cancelled_amount(0)
        ]
        grain = GRAIN + ["channel"]
        grouped = aggregate_by_grain(events, grain, METRIC_COLUMNS)
        self.assertEqual(len(grouped), 2, "channel grain → 2 rows")

        # Validation rows need same fields as before
        validation_rows = []
        for k, v in grouped.items():
            net = (v["qty"] - v["free_qty"] - v["give_qty"]
                   - v["refund_qty"] - v["cancelled_qty"])
            validation_rows.append({
                "store_num": k[0], "item_name": k[3], "price": k[4],
                "channel": k[5],
                "net_qty": net, "revenue": v["actual_amount"], **v,
            })
        result = check(validation_rows, DEFAULT_IDENTITIES)
        self.assertEqual(result.violations, [])


if __name__ == "__main__":
    unittest.main()
