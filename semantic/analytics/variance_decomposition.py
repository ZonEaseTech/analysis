"""Variance decomposition — 差异自动分解 (P5).

最经典: 毛利变化 4 维分解 (量差/价差/成本差/结构差).

定义 (跟管理会计教材一致, "标准成本差异分析"):

  设两期数据 P (previous) 和 C (current):
    q  = 销量
    p  = 单价
    c  = 单位成本
    M  = 销量结构 (各 SKU 占比)

  毛利 = (p − c) × q

  总差异 = C 毛利 − P 毛利

  4 维分解 (Mix-Adjusted Volume × Price × Cost × Mix):
    量差 (Volume) = (C_q − P_q) × P_unit_margin
                    销量变化造成的毛利变化 (假设单价/成本不变)
    价差 (Price)  = (C_p − P_p) × C_q
                    单价变化造成的毛利变化 (吸收量后)
    成本差 (Cost) = (P_c − C_c) × C_q
                    单位成本变化 (注意符号: 成本降 = 毛利升)
    结构差 (Mix)  = 总差异 − 量差 − 价差 − 成本差
                    SKU 占比变化造成的剩余差异

  4 维相加 = 总差异 (恒等).

简化的 single-SKU 版本 (在 mix 不变假设下):
  量差 + 价差 + 成本差 = 总差异
  这种情况 mix 差 = 0.

营收 (revenue) 差异 2 维分解:
  量差 = ΔQ × P_p
  价差 = ΔP × C_q
  量差 + 价差 = 总差异

这一层不实际跑历史 BQ 数据 — 让 caller 提供两期数据 dict, 我们做数学.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Variance:
    """两期变化的单一维度分解项."""

    name: str             # "量差" / "价差" / "成本差" / "结构差"
    amount: float         # 贡献到总差异的金额 (正/负)
    pct_of_total: Optional[float] = None    # 占总差异的比例
    note: str = ""


@dataclass(frozen=True)
class GrossProfitVariance:
    """毛利差异的 4 维分解结果."""

    previous_gp: float
    current_gp: float
    total_delta: float
    volume: Variance       # 量差
    price: Variance        # 价差
    cost: Variance         # 成本差
    mix: Variance          # 结构差 (残差)

    @property
    def variances(self) -> list[Variance]:
        return [self.volume, self.price, self.cost, self.mix]

    def reconciles(self, tolerance: float = 0.01) -> bool:
        """验证 4 维之和 = total_delta. 浮点误差容忍."""
        s = sum(v.amount for v in self.variances)
        return abs(s - self.total_delta) < tolerance


# ───────────────────────────────────────────────────────────────
# Single-SKU / aggregate decomposition
# ───────────────────────────────────────────────────────────────

def decompose_revenue(
    *,
    previous_qty: float,
    previous_price: float,
    current_qty: float,
    current_price: float,
) -> list[Variance]:
    """营收 2 维分解 (量差 + 价差).

    Revenue = qty × price
    ΔR = (C_q − P_q) × P_p + (C_p − P_p) × C_q

    这种"标准量价分解"在两个项之间分母分子不一样有歧义 — 这里用
    "用 previous price 算量差 + 用 current qty 算价差" (Laspeyres-like).
    """
    dq = current_qty - previous_qty
    dp = current_price - previous_price

    volume_var = dq * previous_price
    price_var = dp * current_qty

    return [
        Variance(name="量差", amount=volume_var,
                note=f"ΔQ={dq:+.2f} × P_price={previous_price:.2f}"),
        Variance(name="价差", amount=price_var,
                note=f"ΔP={dp:+.2f} × C_qty={current_qty:.2f}"),
    ]


def decompose_gross_profit(
    *,
    previous_qty: float,
    previous_price: float,
    previous_unit_cost: float,
    current_qty: float,
    current_price: float,
    current_unit_cost: float,
) -> GrossProfitVariance:
    """毛利 4 维分解 (single SKU, mix=0).

    GP = (price − unit_cost) × qty

    ΔGP = (ΔQ × prev_unit_margin)    ← 量差
        + (ΔP × cur_qty)             ← 价差
        + (-ΔC × cur_qty)            ← 成本差 (cost up → GP down)
        + mix_residual               ← 结构差 (single SKU 时 = 0)

    Args:
        previous_*: 上期销量/单价/单位成本
        current_*:  本期销量/单价/单位成本

    Returns:
        GrossProfitVariance, 含 4 个 Variance 项 + 总差异
    """
    prev_unit_margin = previous_price - previous_unit_cost
    cur_unit_margin = current_price - current_unit_cost

    previous_gp = prev_unit_margin * previous_qty
    current_gp = cur_unit_margin * current_qty
    total_delta = current_gp - previous_gp

    # 4 维分解
    dq = current_qty - previous_qty
    dp = current_price - previous_price
    dc = current_unit_cost - previous_unit_cost

    volume_amount = dq * prev_unit_margin
    price_amount = dp * current_qty
    cost_amount = -dc * current_qty
    mix_amount = total_delta - volume_amount - price_amount - cost_amount

    def _pct(amt):
        return amt / total_delta if total_delta else None

    return GrossProfitVariance(
        previous_gp=previous_gp,
        current_gp=current_gp,
        total_delta=total_delta,
        volume=Variance(
            name="量差", amount=volume_amount, pct_of_total=_pct(volume_amount),
            note=f"ΔQ={dq:+.2f} × P_unit_margin={prev_unit_margin:.2f}",
        ),
        price=Variance(
            name="价差", amount=price_amount, pct_of_total=_pct(price_amount),
            note=f"ΔP={dp:+.2f} × C_qty={current_qty:.2f}",
        ),
        cost=Variance(
            name="成本差", amount=cost_amount, pct_of_total=_pct(cost_amount),
            note=(f"ΔC={dc:+.2f} × C_qty={current_qty:.2f} "
                  "(成本上升 → 毛利下降, 符号已反转)"),
        ),
        mix=Variance(
            name="结构差", amount=mix_amount, pct_of_total=_pct(mix_amount),
            note="残差; single-SKU 通常为 0, 跨 SKU 时反映占比变化",
        ),
    )
