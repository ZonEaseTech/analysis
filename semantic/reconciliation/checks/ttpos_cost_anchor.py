"""TtposCostAnchor — 物料单价对账锚: 我们管线的成本价 vs ERP 按 ttpos 算法复算的真值比对.

业务价值:
  - 验证 bom_pipeline / semantic 管线里的物料单价跟 ttpos 后端走同一条算法
  - 揭示"我们算的成本"和"ttpos GetItemUnitCost 算的成本"之间的漂移
  - 给客户"我们的成本口径跟 ttpos 一致"的硬证明（接通 ERP sid 后）

跨进程对账哲学（仿 ttpos_anchor.py）:
  本 check 走"注入式对账"：
    - 纯比对核（run_cost_anchor）不碰 ERP，用 fixture 字典可完整单测
    - ERP 读取适配器（fetch_ttpos_truths_from_erp）支持注入假 erp_get，live 路径需 sid
  优点: 纯核可全量离线测；ERP 侧可 mock 测；live 路径接通 sid 后才跑

ttpos 后端算法真源:
  ttpos-bmp/app/ttpos-erp/internal/logic/stock/item.go
    - calculateFinalItemUnitCost(baseCost, rules, taxRate)    item.go:309
    - applyItemUnitCostPricingRules(baseCost, rules)
    - resolveItemUnitCostTaxRate(netCost, taxes, templateRates)
    - appliesToItemUnitCost(rule, priceList)                  item.go:279-296
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Callable, Optional

from bom_pipeline.erpnext_price import (
    PricingRule,
    calculate_final_item_unit_cost,
    resolve_tax_rate,
    rule_applies,
)

# ERP buying 价表名（固定）
_BUYING_PRICE_LIST = "Buying - Internal"

# PRLE-0003 是 ttpos 后端 loadItemUnitCostPricingRules 固定取的规则 Name
# 完整 PRLE-0003 条件（Task 4.1 补全，对齐 item.go:279-296 的 5 条 guard）：
# Name==PRLE-0003、buying && !disabled、PriceOrDiscount 空或"Price"、
# for_price_list 匹配、valid_from/valid_upto 日期有效性
_PRLE_0003_NAME = "PRLE-0003"


# ---------------------------------------------------------------------------
# Layer 1: 纯函数 — compute_ttpos_unit_cost
# 对齐 item.go:309 calculateFinalItemUnitCost + appliesToItemUnitCost:279-296
# ---------------------------------------------------------------------------
def compute_ttpos_unit_cost(
    base: float,
    margin_pct: float,
    applies: bool,
    tax_rate: float,
) -> float:
    """复算 ttpos calculateFinalItemUnitCost 的终价。纯函数，可无 ERP 单测。

    真源: item.go:309 calculateFinalItemUnitCost + item.go:279-296 appliesToItemUnitCost

    算法:
      1. applies=True  → net = base × (1 + margin_pct/100)   （Percentage margin）
         applies=False → net = base                            （规则不适用，跳过）
      2. net==0 或 tax_rate==0 → return net                   （对齐 Go 短路逻辑）
      3. 否则 → return net × (1 + tax_rate/100)

    Args:
        base:       基价（ERPNext Item Price price_list_rate，按匹配 UOM）
        margin_pct: margin 百分率（e.g. 5.0 表示 5%，对应 PRLE-0003 Percentage 5）
        applies:    该规则是否适用于此物料/价表（appliesToItemUnitCost 结果）
        tax_rate:   Item Tax 税率百分数（0 表示免税；None 场景已在上游转为 DEFAULT 7 传入）

    Returns:
        float: ttpos 口径下的物料终价（与 ERPNext GetItemUnitCost 返回值对齐）
    """
    rules = [PricingRule("Percentage", margin_pct)] if applies else []
    return calculate_final_item_unit_cost(base, rules, tax_rate)


# ---------------------------------------------------------------------------
# Layer 2: TtposCostAnchorResult dataclass + is_drift 属性
# ---------------------------------------------------------------------------
@dataclass
class TtposCostAnchorResult:
    """单物料的成本价对账结果。

    Fields:
        item_code: 物料编码（ERPNext Item Code）
        ours:      我们管线算出的单价
        ttpos:     ERP 按 ttpos 算法复算的真值（或用 run_cost_anchor 注入的 fixture）
        abs_tol:   绝对容差（元）。|ours-ttpos| <= abs_tol → 不漂
        rel_tol:   相对容差（0.001 = 0.1%）。双条件：abs AND rel 都超才算漂

    is_drift 逻辑:
        |ours-ttpos| > abs_tol  且  相对差 > rel_tol → drift=True
        （ttpos=0 时退化为纯绝对差判定：abs_diff > abs_tol → drift）

    注意：run_cost_anchor 产出所有 result（含未漂物料），调用方按需过滤 is_drift=True。
    这里是比对结果，不是"已对齐 ttpos 的证明"——live 路径需接通 ERP sid 才能产真对账。
    """

    item_code: str
    ours: float
    ttpos: float
    abs_tol: float = 0.01
    rel_tol: float = 0.001  # 0.1% 默认相对容差

    @property
    def is_drift(self) -> bool:
        """同时超过 abs_tol 和 rel_tol 才判为漂移（双条件 AND）。"""
        abs_diff = abs(self.ours - self.ttpos)
        if abs_diff <= self.abs_tol:
            return False
        # ttpos=0 时无法算相对差，退化为纯绝对差（已超 abs_tol）→ drift
        if self.ttpos == 0:
            return True
        rel_diff = abs_diff / abs(self.ttpos)
        return rel_diff > self.rel_tol


# ---------------------------------------------------------------------------
# Layer 3: run_cost_anchor — 纯比对，不碰 ERP
# ---------------------------------------------------------------------------
def run_cost_anchor(
    our_prices: dict[str, float],
    ttpos_truths: dict[str, float],
    *,
    abs_tol: float = 0.01,
    rel_tol: float = 0.001,
) -> list[TtposCostAnchorResult]:
    """对两个 {item_code: price} dict 逐物料比对，返回所有交集物料的 result。

    纯比对核：不碰 ERP，不读任何外部资源。给两个 fixture 字典可完整离线单测。
    调用方用 [r for r in results if r.is_drift] 筛漂移物料。

    注意：这是比对计算结果，不代表"已对齐 ttpos"——live ERP 数据需接通 sid 获取
    (见 fetch_ttpos_truths_from_erp 的说明)。

    Args:
        our_prices:   我们管线的物料单价字典 {item_code: price}
        ttpos_truths: ERP 复算的 ttpos 真值字典 {item_code: price}
                      （可由 fetch_ttpos_truths_from_erp 产出，或用 fixture 注入）
        abs_tol:      绝对容差（元）
        rel_tol:      相对容差

    Returns:
        list[TtposCostAnchorResult]：只含 our_prices ∩ ttpos_truths 的物料，
        单边缺失（only in ours / only in truths）跳过，不产出 result。
    """
    results = []
    for item_code, our_price in our_prices.items():
        if item_code not in ttpos_truths:
            # 只在我们这边有，ttpos_truths 里没有 → 无法比对，跳过
            continue
        results.append(
            TtposCostAnchorResult(
                item_code=item_code,
                ours=our_price,
                ttpos=ttpos_truths[item_code],
                abs_tol=abs_tol,
                rel_tol=rel_tol,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Layer 4: fetch_ttpos_truths_from_erp — ERP 读取适配器（erp_get 可注入）
# ---------------------------------------------------------------------------
def fetch_ttpos_truths_from_erp(
    item_codes: list[str],
    *,
    sid: Optional[str] = None,
    erp_get: Optional[Callable[..., list[dict]]] = None,
) -> dict[str, float]:
    """从 ERP 读取 Item Price + Pricing Rule + Item Tax，按 ttpos 算法复算各物料真值。

    完整 PRLE-0003 条件（Task 4.1 忠实复刻 Go appliesToItemUnitCost，item.go:279-296）：
      Go 的 5 条 guard 全部覆盖：
      1. Name == "PRLE-0003"                          （item.go:280）
      2. buying == True/1  且  !disabled              （item.go:283，委托 rule_applies）
      3. PriceOrDiscount 空 或 == "Price"（大小写不敏感）（item.go:286）
      4. for_price_list 空 或 匹配 "Buying - Internal"  （item.go:289，委托 rule_applies）
      5. valid_from 空或 <= 今天 且 valid_upto 空或 >= 今天（item.go:292/298-307）

    Task 2.1 的 rule_applies 只做了子集（guard 2、4），明确把 Name / PriceOrDiscount /
    日期有效性留到本层（Task 4.1）补全。本函数：guard 1/3/5 在此实现，guard 2/4 委托
    bom_pipeline.erpnext_price.rule_applies。至此 Go 的 5 条**规则适用 guard** 真全
    （注意：这"真全"只覆盖规则适用性 appliesToItemUnitCost，不含税率侧，见下方落差声明）。

    ⚠️ 诚实性声明 1（税率侧为简化模型，非忠实复刻）：
      税率取数直读 Item Tax 的 `tax_rate` 字段，**未**复刻 Go resolveItemUnitCostTaxRate
      （item.go:332-348）的两点：
        ① 按 post-margin netCost 落在 tax.MinimumNetRate / MaximumNetRate 区间筛选；
        ② Item Tax Template 间接层（item_tax_template → Item Tax Template doctype →
           sum(taxes[].tax_rate)，见 loadItemUnitCostTaxTemplateRates item.go:241-266）。
      华莱士单一 7% VAT 场景下两者数值等价（"碰巧相等"），但接其他税制 / 分档税率前
      必须补全上述逻辑，否则税率会算错。

    ⚠️ 诚实性声明 2（live 路径）：
      erp_get=None 时需要有效的 ERP sid 才能从 erpnext_api 真实读取；当前 sid 失效未接入，
      本函数对 erp_get=None 直接抛 NotImplementedError（不留坏 import），接通 sid 后才能
      产出真对账数据。本轮测试不跑 live 路径，见 test_ttpos_cost_anchor.py Layer 4。

    Args:
        item_codes: 要查的物料编码列表
        sid:        ERP session ID（live 路径用，注入假 erp_get 时可忽略）
        erp_get:    可注入的 ERP 查询 callable (doctype, **kwargs) -> list[dict]。
                    None → live 路径，当前抛 NotImplementedError（需 sid，未接入）。
                    测试/离线时注入假实现（见 tests/test_ttpos_cost_anchor.py）。
                    读 Pricing Rule 时返回的 row 需含 price_or_product_discount 字段
                    （live 实现接入时从 ERPNext Pricing Rule doctype 取该列）。

    Returns:
        dict[str, float]: {item_code: ttpos_unit_cost}
        ERP 里没有 Item Price 的物料不出现在结果里（调用方可按需处理缺失）。
    """
    if erp_get is None:
        # ⚠️ live 路径：需要有效 ERP sid，当前未接入，本轮不在测试里跑。
        # 将来接通 sid 后，从 bq_reports.utils.erpnext_api 用 load_erpnext_prices /
        # load_erpnext_item_last_purchase 读 Buying-Internal Item Price，并补 Pricing Rule
        # (PRLE-0003) + Item Tax 的拉取，包成 erp_get(doctype, **kwargs) 形态注入。
        # 不留坏 import：erpnext_api 当前没有通用 get_list，直接据实抛 NotImplementedError。
        raise NotImplementedError(
            "fetch_ttpos_truths_from_erp 的 live ERP 读取需 sid;"
            "当前 sid 失效未接入,接 sid 后实现真实读取 Buying-Internal Item Price + "
            "PRLE-0003 + Item Tax(见 bq_reports.utils.erpnext_api.load_erpnext_prices)。"
            "测试/离线请传入 erp_get= 注入假数据。"
        )

    today = datetime.date.today()

    # Step 1: 拉 Item Price（Buying - Internal 价表）
    ip_rows: list[dict] = erp_get("Item Price", price_list=_BUYING_PRICE_LIST)
    # {item_code: rate}，只保留目标物料
    item_code_set = set(item_codes)
    base_prices: dict[str, float] = {}
    for row in ip_rows:
        code = row.get("item_code", "")
        if code in item_code_set:
            base_prices[code] = float(row.get("price_list_rate", 0.0))

    # Step 2: 拉 Pricing Rule（只取 PRLE-0003，完整条件过滤）
    rule_rows: list[dict] = erp_get("Pricing Rule")
    applicable_rules: list[PricingRule] = []
    for row in rule_rows:
        # 条件 1: Name == "PRLE-0003"（item.go:280）
        if row.get("name") != _PRLE_0003_NAME:
            continue
        # 条件 2: PriceOrDiscount 空 或 == "Price"（大小写不敏感）。
        # 对齐 item.go:286: r.PriceOrDiscount != "" && !EqualFold("Price") → false。
        # Task 4.1 补全的 fidelity 条件（Task 2.1 故意留到本层）。
        pod = (row.get("price_or_product_discount") or "").strip()
        if pod and pod.lower() != "price":
            continue  # 非 Price 型规则（如 Discount）→ 不套
        # 条件 3-5: buying、!disabled、for_price_list（委托 rule_applies）
        # 停用标志：ERPNext Pricing Rule doctype 真实字段名为 `disable`（无 d 结尾，
        # item.go:229 data.Get("disable")）。优先读 disable，回退内部 fixture 的 disabled，
        # 避免接 sid 后真实行的 disable=1 被漏读、把已停用规则当启用错套 margin。
        disabled_flag = row.get("disable", row.get("disabled", False))
        rule = PricingRule(
            margin_type=row.get("margin_type", ""),
            margin_rate_or_amount=float(row.get("margin_rate_or_amount", 0.0)),
            for_price_list=row.get("for_price_list", ""),
            buying=bool(row.get("buying", True)),
            disabled=bool(disabled_flag),
        )
        if not rule_applies(rule, _BUYING_PRICE_LIST):
            continue
        # 条件 5a: valid_from 空或 <= 今天（item.go:300）
        valid_from_str = (row.get("valid_from") or "").strip()
        if valid_from_str:
            try:
                valid_from = datetime.date.fromisoformat(valid_from_str[:10])
                if valid_from > today:
                    continue  # 规则尚未生效
            except ValueError:
                pass  # 无法解析则忽略日期限制（宽松）
        # 条件 5b: valid_upto 空或 >= 今天（item.go:303）
        valid_upto_str = (row.get("valid_upto") or "").strip()
        if valid_upto_str:
            try:
                valid_upto = datetime.date.fromisoformat(valid_upto_str[:10])
                if valid_upto < today:
                    continue  # 规则已过期
            except ValueError:
                pass  # 无法解析则忽略日期限制（宽松）
        applicable_rules.append(rule)

    # Step 3: 拉 Item Tax
    tax_rows: list[dict] = erp_get("Item Tax")
    item_taxes: dict[str, float] = {}
    for row in tax_rows:
        code = row.get("item_code", "")
        if code in item_code_set:
            item_taxes[code] = float(row.get("tax_rate", 0.0))

    # Step 4: 逐物料复算 ttpos 终价
    results: dict[str, float] = {}
    for code in item_codes:
        if code not in base_prices:
            # ERP 里没有这个物料的 Item Price → 跳过
            continue
        base = base_prices[code]
        # tax_rate: 有 Item Tax → 用配置税率；无 → 兜底 7%（resolve_tax_rate(None) = 7）
        raw_tax = item_taxes.get(code)
        tax_rate = resolve_tax_rate(None if raw_tax is None else raw_tax)
        # 按 ttpos 算法套用 applicable_rules（可能空列表）再上浮税：直接走
        # calculate_final_item_unit_cost（item.go:309），避免内联展开与之口径漂移。
        results[code] = calculate_final_item_unit_cost(base, applicable_rules, tax_rate)

    return results
