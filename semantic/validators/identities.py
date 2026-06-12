"""Built-in identities for the profit-margin family of reports.

Thresholds in `_money_classify` are placeholders tuned for ttpos
(unit: THB / CNY). After running on real data, adjust the constants here.

Don't define identities outside this file unless they're truly one-off —
the value of having ONE place is greater than the convenience of
report-local rules.

Identity families:
  - DEFAULT_IDENTITIES        : 数学恒等式（销量、金额）
  - SOURCE_COVERAGE_IDENTITIES: 来源完整性 (P2 新增)
  - SANITY_BAND_IDENTITIES    : 业务合理性区间 (P2 新增)
  - FULL_IDENTITIES           : 全部启用 (默认 + source 覆盖 + sanity)
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

# ⚠️ 定义式守卫, 非对账. 报表层的 net_qty 是 `qty − 其他桶` 减法推导的,
# 本恒等式在这种 row 上代数永真 (见 tests/test_identity_perturbation.py
# TestSalesQtyIsDefinitional). 它的价值仅剩: 防字段缺失 / schema 漂移.
# 真实检测力 = CROSS_LEDGER_IDENTITIES (统计账 vs 凭证账互证).
SALES_QTY_IDENTITY = Identity(
    name="销量恒等式",
    description="qty = net_qty + free_qty + give_qty + refund_qty + cancelled_qty",
    lhs=lambda r: r["qty"],
    rhs=lambda r: (r["net_qty"] + r["free_qty"] + r["give_qty"]
                   + r["refund_qty"] + r["cancelled_qty"]),
    classify=_qty_classify,
    fields=("qty", "net_qty", "free_qty", "give_qty", "refund_qty", "cancelled_qty"),
)

AMOUNT_IDENTITY = Identity(
    name="金额恒等式",
    description=(
        "sales_price = revenue + refund + free + give + discount\n"
        "（cancelled_amount 不参与：ttpos sales_price 已按 state=60 排除，"
        "取消件价格由 GROSS_AMOUNT_IDENTITY 闭环审计）"
    ),
    lhs=lambda r: r["sales_price"],
    rhs=lambda r: (r["revenue"] + r["refund_amount"]
                   + r["free_amount"] + r["give_amount"]
                   + r["discount_amount"]),
    classify=_money_classify,
    fields=("sales_price", "revenue", "refund_amount", "free_amount", "give_amount", "discount_amount"),
)


GROSS_AMOUNT_IDENTITY = Identity(
    name="毛额守恒恒等式",
    description=(
        "gross_amount = sales_price + cancelled_amount\n"
        "守恒闭环: 把被金额恒等式排除的取消金额纳入审计 (spec §5 A3). 外卖侧"
        " gross 不分 state 全量, 本式审计 state 枚举完备性 — ttpos 若新增 state,"
        " 金额漏桶立刻 fire."
    ),
    lhs=lambda r: r["gross_amount"],
    rhs=lambda r: r["sales_price"] + r["cancelled_amount"],
    classify=_money_classify,
    fields=("gross_amount", "sales_price", "cancelled_amount"),
)


# Default bundle for the profit_margin / sku_profit_summary reports.
DEFAULT_IDENTITIES = [SALES_QTY_IDENTITY, AMOUNT_IDENTITY, GROSS_AMOUNT_IDENTITY]


# ═══════════════════════════════════════════════════════════════════
# P2 — Source Coverage Identities (来源完整性)
#
# 报表行经 P1 Resolver 解析后, agg_data 上会带 bom_source / price_source
# 字段 (由 _annotate_agg_data_sources 写入). 这些 identity 校验"有销量但
# 没找到 BOM 来源"这种数据缺失.
# ═══════════════════════════════════════════════════════════════════

def _coverage_classify(delta: float, lhs: float) -> Severity:
    """0 = 完整 (NEGLIGIBLE), 1 = 缺失 (MUST_FIX)。

    跟 _money_classify / _qty_classify 不同, coverage 是 binary —— 任何缺失
    都是 must-fix, 没有 NEEDS_REVIEW 中间档.
    """
    return Severity.NEGLIGIBLE if delta == 0 else Severity.MUST_FIX


BOM_SOURCE_COVERAGE = Identity(
    name="BOM 来源完整性",
    description=(
        "有销量 (qty > 0) 的 SKU 必须有 BOM 来源 (bom_source != '无').\n"
        "缺失意味着这个 SKU 没匹配上任何 BOM 数据源, 物料成本会算成 0.\n"
        "Opt-in: 只在 row 显式有 bom_source 字段时检查 (profit_by_price 不走 BOM)."
    ),
    # lhs = 1 if 缺失 else 0; rhs = 0; delta != 0 即缺失
    # 只对显式带 bom_source 字段的 row 检查 (opt-in)
    lhs=lambda r: 1.0 if (
        "bom_source" in r
        and float(r.get("qty", 0) or 0) > 0
        and r.get("bom_source", "无") == "无"
    ) else 0.0,
    rhs=lambda r: 0.0,
    classify=_coverage_classify,
)


PRICE_SOURCE_COVERAGE = Identity(
    name="物料单价来源完整性",
    description=(
        "有 BOM 物料的 SKU 必须有物料单价来源 (price_source != '无').\n"
        "缺失意味着 BOM 找到了但单价全部 fallback 到 bq_native 或 0, 成本不可信.\n"
        "Opt-in: 只在 row 显式有 price_source 字段时检查."
    ),
    lhs=lambda r: 1.0 if (
        "price_source" in r
        and r.get("bom_source", "无") != "无"
        and r.get("price_source", "无") == "无"
    ) else 0.0,
    rhs=lambda r: 0.0,
    classify=_coverage_classify,
)


SOURCE_COVERAGE_IDENTITIES = [
    BOM_SOURCE_COVERAGE,
    PRICE_SOURCE_COVERAGE,
]


# ═══════════════════════════════════════════════════════════════════
# P2 — Sanity Band Identities (业务合理性区间)
#
# 不是数学恒等式, 是"业务上数字应该在某区间内"的检查. 比如退款率应该 < 5%.
# 越界不一定数据错, 但值得人工 review. 走 NEEDS_REVIEW (🟡) 而不是 MUST_FIX,
# 除非越界离谱.
# ═══════════════════════════════════════════════════════════════════

def _band_classify(low: float, high: float,
                   hard_low: float = None, hard_high: float = None):
    """构造一个区间检查 classify 函数。

    [low, high]          → NEGLIGIBLE (OK)
    [hard_low, low)
      或 (high, hard_high] → NEEDS_REVIEW (🟡 复核)
    < hard_low / > hard_high → MUST_FIX (🔴 离谱)

    Args:
        low, high: 健康区间
        hard_low, hard_high: hard 离谱区间. 不传时默认软区间外 2x.

    用法:
        Identity(..., classify=_band_classify(0.0, 0.05, hard_high=0.20))
        # 期望 < 5%; 5-20% 黄色; > 20% 红色
    """
    if hard_low is None:
        hard_low = float("-inf")
    if hard_high is None:
        hard_high = float("inf")

    def classify(_delta: float, lhs: float) -> Severity:
        val = lhs  # band 检查里 lhs 就是 value, delta 没意义
        if low <= val <= high:
            return Severity.NEGLIGIBLE
        if val < hard_low or val > hard_high:
            return Severity.MUST_FIX
        return Severity.NEEDS_REVIEW

    return classify


REFUND_RATIO_BAND = Identity(
    name="退款率合理性",
    description=(
        "退款率 (refund_qty / qty) 应 < 5%; > 20% 必查.\n"
        "高退款率可能是: 商品质量问题 / 收银录单错 / 顾客大量退订单."
    ),
    lhs=lambda r: (
        float(r.get("refund_qty", 0) or 0) / float(r["qty"])
        if float(r.get("qty", 0) or 0) > 0 else 0.0
    ),
    rhs=lambda r: 0.0,
    classify=_band_classify(0.0, 0.05, hard_high=0.20),
)


FREE_GIVE_RATIO_BAND = Identity(
    name="赠品赠送率合理性",
    description=(
        "(赠品 + 赠送) / 销量 应 < 10%; > 30% 必查.\n"
        "高赠送率可能是: 活动促销 (正常) / 员工权限滥用 / 录单走赠送绕收银."
    ),
    lhs=lambda r: (
        (float(r.get("free_qty", 0) or 0) + float(r.get("give_qty", 0) or 0))
        / float(r["qty"])
        if float(r.get("qty", 0) or 0) > 0 else 0.0
    ),
    rhs=lambda r: 0.0,
    classify=_band_classify(0.0, 0.10, hard_high=0.30),
)


CANCEL_RATIO_BAND = Identity(
    name="外卖取消率合理性",
    description=(
        "外卖取消率 (cancelled_qty / qty) 应 < 10%; > 30% 必查.\n"
        "高取消率可能是: 商品缺货频繁 / 商家拒单 / 平台账号异常."
    ),
    lhs=lambda r: (
        float(r.get("cancelled_qty", 0) or 0) / float(r["qty"])
        if float(r.get("qty", 0) or 0) > 0 else 0.0
    ),
    rhs=lambda r: 0.0,
    classify=_band_classify(0.0, 0.10, hard_high=0.30),
)


SANITY_BAND_IDENTITIES = [
    REFUND_RATIO_BAND,
    FREE_GIVE_RATIO_BAND,
    CANCEL_RATIO_BAND,
]


# ═══════════════════════════════════════════════════════════════════
# A1 — Cross-Ledger Identities (跨账本互证: 统计账 vs 凭证账)
#
# row 由 semantic/validators/cross_ledger.build_cross_ledger_rows 构建.
# ⚠️ 独立 bundle, 不进 FULL_IDENTITIES / 不进导出闸门 — 时间语义对齐度
# (sp.complete_time vs sb.finish_time) 由 2026-05 观察跑实测后才决定升级
# (spec §11 PR-A 验收线; 决策记录见 docs/audit/2026-06-cross-ledger-baseline.md).
# ═══════════════════════════════════════════════════════════════════

CROSS_LEDGER_QTY = Identity(
    name="跨账本销量互证",
    description="统计账 qty == 凭证账 SUM(num) — 两本账独立写入, 互相可证伪",
    lhs=lambda r: r["stat_qty"],
    rhs=lambda r: r["voucher_qty"],
    classify=lambda d, lhs: (Severity.NEGLIGIBLE if d == 0 else Severity.MUST_FIX),
    fields=("stat_qty", "voucher_qty"),
)

CROSS_LEDGER_GROSS = Identity(
    name="跨账本毛额互证",
    description="统计账 gross_amount == 凭证账 SUM(sale_price×num) — PR-B 整数化后收零容差",
    lhs=lambda r: r["stat_gross"],
    rhs=lambda r: r["voucher_gross"],
    classify=_money_classify,
    fields=("stat_gross", "voucher_gross"),
)

VOUCHER_COVERAGE = Identity(
    name="凭证账覆盖完整性",
    description=(
        "统计账有销量 (stat_qty > 0) 的 (store, item) 必须在凭证账有行.\n"
        "违反语义是「该数字未经互证」, 不是「数字错了」— console 文案要区分."
    ),
    lhs=lambda r: 1.0 if (r["stat_qty"] > 0 and r["voucher_present"] == 0.0) else 0.0,
    rhs=lambda r: 0.0,
    classify=_coverage_classify,
    fields=("stat_qty", "voucher_present"),
)

CROSS_LEDGER_IDENTITIES = [CROSS_LEDGER_QTY, CROSS_LEDGER_GROSS, VOUCHER_COVERAGE]


# ═══════════════════════════════════════════════════════════════════
# Combined bundles
# ═══════════════════════════════════════════════════════════════════

# Full bundle — all P1 + P2 identities. 推荐生产用这个.
FULL_IDENTITIES = (
    DEFAULT_IDENTITIES
    + SOURCE_COVERAGE_IDENTITIES
    + SANITY_BAND_IDENTITIES
)
