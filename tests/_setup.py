"""Shared test bootstrap: put repo root on sys.path.

Imported (not `from … import *`) by every test module so that
`python -m unittest discover tests` works from any CWD.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def order_row(**fields) -> SimpleNamespace:
    """Build a synthetic order row matching the shape `aggregate_with_bom` reads.

    All numeric fields default to 0 / None so tests only override what they care
    about. Use this instead of hand-rolling SimpleNamespace each time to keep
    tests robust to future schema additions.
    """
    defaults = dict(
        store_num="001", store_name="店A",
        item_uuid="100", item_name="商品A",
        qty=0, revenue=0,
        sales_price=0, original_amount=0, avg_member_discount=1.0,
        free_qty=0, give_qty=0, refund_qty=0, refund_amount=0,
        cancelled_qty=0, cancelled_amount=0,
        # 金额恒等式分项（堂食 sale_line 才有非零；外卖固定 0）
        free_amount=0, give_amount=0, discount_amount=0,
        list_price=0,
        price_1=None, qty_1=None,
        price_2=None, qty_2=None,
        price_3=None, qty_3=None,
        other_price_qty=None,
    )
    defaults.update(fields)
    return SimpleNamespace(**defaults)
