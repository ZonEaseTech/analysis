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


# ─── Money identity thresholds (萨当单位) ────────────────────────────
# PR-B 7c: 交易金额整数化萨当后, 单位从「元」改为「萨当」(1 元 = 100 萨当).
# 这些常数已不服务 sum 型金额恒等式 (AMOUNT/GROSS 现走 _exact_satang_classify,
# delta==0 零容差) — 仅供 CROSS_LEDGER_GROSS (两本账残差是数据级, 仍带容忍带)
# 和封顶勾稽 (_capped_review_money) 用.
_NEGLIGIBLE_ABS = 1          # <1 萨当 (= 1 分钱)：精度, 无视
_NEGLIGIBLE_REL = 0.001      # <0.1%：舍入累积, 无视
_NEGLIGIBLE_ABS_LOOSE = 100  # <100 萨当 (= 1 元) 且 <0.1%：累积舍入, 无视
_MUST_FIX_ABS = 10_000       # >10000 萨当 (= 100 元)：必查
_MUST_FIX_REL = 0.05         # >5%：必查


def _money_classify(delta: float, lhs: float) -> Severity:
    """Two-axis classifier: absolute AND relative thresholds compete.

    NEGLIGIBLE: tiny absolute OR tiny absolute+relative combo (累积舍入)
    MUST_FIX:   large absolute OR large relative
    Else:       NEEDS_REVIEW

    单位 = 萨当 (PR-B 7c). 服务两本账残差类对账 (CROSS_LEDGER) + 封顶勾稽,
    不服务 sum 型金额恒等式 (那些走 _exact_satang_classify 收零).
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


def _exact_satang_classify(delta: float, lhs: float) -> Severity:
    """整数萨当算术精确 — 零是唯一可接受答案 (spec §6 B).

    sum 型金额恒等式 (AMOUNT/GROSS) 的两边都是同一份萨当整数的加减组合,
    整数化后代数上必然精确相等. 任何非零 delta = 真 bug (字段缺失 / SQL 漏桶 /
    口径漂移), 立即 MUST_FIX, 没有容忍带.
    """
    return Severity.NEGLIGIBLE if delta == 0 else Severity.MUST_FIX


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
        "取消件价格由 GROSS_AMOUNT_IDENTITY 闭环审计）\n"
        "(萨当整数, delta==0)"
    ),
    lhs=lambda r: r["sales_price"],
    rhs=lambda r: (r["revenue"] + r["refund_amount"]
                   + r["free_amount"] + r["give_amount"]
                   + r["discount_amount"]),
    classify=_exact_satang_classify,
    fields=("sales_price", "revenue", "refund_amount", "free_amount", "give_amount", "discount_amount"),
)


GROSS_AMOUNT_IDENTITY = Identity(
    name="毛额守恒恒等式",
    description=(
        "gross_amount = sales_price + cancelled_amount\n"
        "守恒闭环: 把被金额恒等式排除的取消金额纳入审计 (spec §5 A3). 外卖侧"
        " gross 不分 state 全量, 本式审计 state 枚举完备性 — ttpos 若新增 state,"
        " 金额漏桶立刻 fire.\n"
        "(萨当整数, delta==0)"
    ),
    lhs=lambda r: r["gross_amount"],
    rhs=lambda r: r["sales_price"] + r["cancelled_amount"],
    classify=_exact_satang_classify,
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
    classify=lambda d, lhs: (Severity.NEGLIGIBLE if d == 0 else Severity.MUST_FIX),  # qty 是 int 转 float, 精确相等安全; 若引入小数计量需改判别
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
# A2 — Takeout Tieout Identities (外卖订单勾稽: item 级求和 vs 订单级 platform_total)
#
# row 由 semantic/entities/takeout_tieout.takeout_tieout_cte() 构建, 订单粒度.
# ⚠️ 独立 bundle, 不进 FULL_IDENTITIES / DEFAULT_IDENTITIES —
#    merchant_charge_fee/merchant_discount 符号关系待观察跑校准 (pitfalls §5.1).
#    华莱士当前两字段恒 0; 业务开启费用时本 bundle 会 fire 提醒校准.
#    封顶 🟡: 升 MUST_FIX 须等口径校准
#    (docs/audit/2026-06-cross-ledger-baseline.md).
# ═══════════════════════════════════════════════════════════════════

def _capped_review_money(delta: float, lhs: float) -> Severity:
    """金额分类但封顶 NEEDS_REVIEW — 给口径未校准的勾稽用 (spec §5 A1 支付勾稽同策)."""
    sev = _money_classify(delta, lhs)
    return Severity.NEEDS_REVIEW if sev == Severity.MUST_FIX else sev


TAKEOUT_TIEOUT_IDENTITY = Identity(
    name="外卖订单勾稽",
    description=(
        "外卖订单级勾稽. 符号待观察跑校准 — 当前假设:\n"
        "platform_total == item_sum − merchant_charge_fee − merchant_discount\n"
        "(华莱士当前 merchant 字段恒 0, 两种符号数值等价; 字段启用时以实测为准).\n"
        "封顶 🟡: 升 MUST_FIX 须等口径校准 (docs/audit/2026-06-cross-ledger-baseline.md)."
    ),
    lhs=lambda r: r["platform_total"],
    rhs=lambda r: (r["item_sum"] - r["merchant_charge_fee"]
                   - r["merchant_discount"]),
    classify=_capped_review_money,
    fields=("platform_total", "item_sum", "merchant_charge_fee", "merchant_discount"),
)

TAKEOUT_TIEOUT_IDENTITIES = [TAKEOUT_TIEOUT_IDENTITY]


PAYMENT_TIEOUT_IDENTITY = Identity(
    name="支付勾稽",
    description=(
        "店×月粒度: SUM(sale_bill.payment_amount) vs 统计账实收 (actual_amount).\n"
        "封顶 🟡 (spec §5 A1): service_fee/tax_fee/整单折扣的口径映射待"
        " 2026-05 观察跑校准, 校准前不转红线 (CLAUDE.md 技术债 ②).\n"
        "row 字段: payment_amount_sum / stat_actual_sum (由观察跑/报表层构造)."
    ),
    lhs=lambda r: r["payment_amount_sum"],
    rhs=lambda r: r["stat_actual_sum"],
    classify=_capped_review_money,
    fields=("payment_amount_sum", "stat_actual_sum"),
)


# ═══════════════════════════════════════════════════════════════════
# 非销售导出基线 (BOM / 菜单 / 名录类报表)
#
# 这些报表没有销量/金额桶, "可靠"的最低数学含义是: 行非空 (gate min_rows)、
# 必填列非空、主键不重复. 比裸奔强一个数量级, 但明确不是对账 — 描述里写清.
# ═══════════════════════════════════════════════════════════════════

def make_required_fields_identity(required: tuple, name: str) -> Identity:
    """必填字段非空基线. row 缺 key 或值为空串/None → MUST_FIX."""
    def _missing_count(r: dict) -> float:
        return float(sum(
            1 for f in required
            if f not in r or r[f] is None or (isinstance(r[f], str) and not r[f].strip())
        ))
    return Identity(
        name=name,
        description=f"必填字段非空: {', '.join(required)} (基线校验, 非对账)",
        lhs=_missing_count,
        rhs=lambda r: 0.0,
        classify=_coverage_classify,
        fields=tuple(required),
    )


def make_unique_key_identity(key_fields: tuple, name: str):
    """主键唯一基线. 返回 (identity, prepare) — prepare 给每行标注 _dup_count,
    identity 检查它为 0. (core.check 是 row-local 的, 跨行去重只能预处理.)"""
    def prepare(rows: list) -> list:
        seen: dict = {}
        for r in rows:
            k = tuple(r.get(f) for f in key_fields)
            seen[k] = seen.get(k, 0) + 1
        return [{**r, "_dup_count": float(seen[tuple(r.get(f) for f in key_fields)] - 1)}
                for r in rows]
    ident = Identity(
        name=name,
        description=f"主键唯一: ({', '.join(key_fields)}) 重复 = 数据放大, MUST_FIX",
        lhs=lambda r: r["_dup_count"],
        rhs=lambda r: 0.0,
        classify=_coverage_classify,
        fields=("_dup_count",),
    )
    return ident, prepare


# ═══════════════════════════════════════════════════════════════════
# Combined bundles
# ═══════════════════════════════════════════════════════════════════

# Full bundle — all P1 + P2 identities. 推荐生产用这个.
FULL_IDENTITIES = (
    DEFAULT_IDENTITIES
    + SOURCE_COVERAGE_IDENTITIES
    + SANITY_BAND_IDENTITIES
)
