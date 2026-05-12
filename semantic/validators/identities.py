"""Built-in identities for the profit-margin family of reports.

Thresholds in `_money_classify` are placeholders tuned for ttpos
(unit: THB / CNY). After running on real data, adjust the constants here.

Don't define identities outside this file unless they're truly one-off —
the value of having ONE place is greater than the convenience of
report-local rules.
"""
from .core import Identity, Severity


# ─── Money identity thresholds (tweak after first real run) ──────────
_NEGLIGIBLE_ABS = 0.01      # <1 分钱：浮点精度，无视
_NEGLIGIBLE_REL = 0.001     # <0.1%：舍入累积，无视
_NEGLIGIBLE_ABS_LOOSE = 1   # <1 元 且 <0.1%：累积舍入，无视
_MUST_FIX_ABS = 100         # >100 元：必查
_MUST_FIX_REL = 0.05        # >5%：必查


def _money_classify(delta: float, lhs: float) -> Severity:
    """Two-axis classifier: absolute AND relative thresholds compete.

    NEGLIGIBLE: tiny absolute OR tiny absolute+relative combo (累积舍入)
    MUST_FIX:   large absolute OR large relative
    Else:       NEEDS_REVIEW
    """
    abs_d = abs(delta)
    rel = (abs_d / abs(lhs)) if lhs else 0.0
    if abs_d < _NEGLIGIBLE_ABS:
        return Severity.NEGLIGIBLE
    if abs_d < _NEGLIGIBLE_ABS_LOOSE and rel < _NEGLIGIBLE_REL:
        return Severity.NEGLIGIBLE
    if abs_d > _MUST_FIX_ABS or rel > _MUST_FIX_REL:
        return Severity.MUST_FIX
    return Severity.NEEDS_REVIEW


def _qty_classify(delta: float, lhs: float) -> Severity:
    """Integer accounting: zero is the only acceptable answer.

    Any non-zero delta is a defect (data drift OR SQL bug). Surfaces as
    MUST_FIX immediately — there's no tolerance band for counts.
    """
    return Severity.NEGLIGIBLE if delta == 0 else Severity.MUST_FIX


# ─── The two identities ──────────────────────────────────────────────

SALES_QTY_IDENTITY = Identity(
    name="销量恒等式",
    description="qty = net_qty + free_qty + give_qty + refund_qty + cancelled_qty",
    lhs=lambda r: r["qty"],
    rhs=lambda r: (r["net_qty"] + r["free_qty"] + r["give_qty"]
                   + r["refund_qty"] + r["cancelled_qty"]),
    classify=_qty_classify,
)

AMOUNT_IDENTITY = Identity(
    name="金额恒等式",
    description=(
        "sales_price = revenue + refund + free + give + discount\n"
        "（cancelled_amount 不参与：ttpos sales_price 已按 state=60 排除，"
        "取消件价格独立审计）"
    ),
    lhs=lambda r: r["sales_price"],
    rhs=lambda r: (r["revenue"] + r["refund_amount"]
                   + r["free_amount"] + r["give_amount"]
                   + r["discount_amount"]),
    classify=_money_classify,
)


# Default bundle for the profit_margin / sku_profit_summary reports.
DEFAULT_IDENTITIES = [SALES_QTY_IDENTITY, AMOUNT_IDENTITY]
