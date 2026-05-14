"""Reconciliation kernel — Check Protocol + Result + Discrepancy + run_checks.

设计要点:
  - Check 是 Protocol (不是 ABC), 任何带 .name + .run() 的对象都行
  - run() 是无参函数, 它自己持有 source / target / tolerance 配置
  - Result.severity 跟 validators.core.Severity 同语义 (NEGLIGIBLE/REVIEW/MUST_FIX)
  - Discrepancy 是单条差异条目 (entity_id + bq_value + external_value + delta + note)

跟 validators 的区别:
  - validators: row-level 数学恒等式 (单期内对账)
  - reconciliation: 跨系统多源对账 (BQ vs ERP / vs 财务 / vs 银行)
  这两层都有"对账"语义但作用域不同, 不互相替代.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Optional, Protocol, runtime_checkable


class ReconciliationSeverity(IntEnum):
    """对账差异严重度. 跟 validators.core.Severity 平行."""

    NEGLIGIBLE = 0    # 差额可忽略 (< 0.01% / < ¥1)
    NEEDS_REVIEW = 1  # 🟡 需复核 (>0.1% / >¥100)
    MUST_FIX = 2      # 🔴 离谱 (>1% / >¥1000)


@dataclass(frozen=True)
class Discrepancy:
    """对账差异单条目.

    entity_id 是被比较单位的稳定标识 (店编号 / 月份 / SKU code 等).
    """

    entity_id: str
    bq_value: float
    external_value: float
    note: str = ""

    @property
    def delta(self) -> float:
        return self.bq_value - self.external_value

    @property
    def relative_delta(self) -> Optional[float]:
        """相对差额. external 为 0 时返回 None."""
        if not self.external_value or self.external_value == 0:
            return None
        return self.delta / abs(self.external_value)


@dataclass
class ReconciliationResult:
    """一个 Check 的运行结果."""

    check_name: str
    total_compared: int                # 比较了多少条目
    discrepancies: list[Discrepancy] = field(default_factory=list)
    severity: ReconciliationSeverity = ReconciliationSeverity.NEGLIGIBLE
    summary: str = ""                  # 一句话总结 (给 console 输出)

    @property
    def matched(self) -> int:
        """完全匹配的条目数."""
        return self.total_compared - len(self.discrepancies)

    @property
    def has_must_fix(self) -> bool:
        return self.severity == ReconciliationSeverity.MUST_FIX

    def discrepancies_by_severity(self, sev: ReconciliationSeverity) -> list[Discrepancy]:
        """按严重度筛差异. severity 默认整个 Result 一级,
        如果要 per-discrepancy severity, Check 实现里可以扩展 Discrepancy.
        """
        if self.severity == sev:
            return self.discrepancies
        return []


@runtime_checkable
class ReconciliationCheck(Protocol):
    """Check 协议. 任何带 name + run() 的对象都行."""

    name: str

    def run(self) -> ReconciliationResult:
        ...


def run_checks(
    checks: list[ReconciliationCheck],
    on_progress: Optional[Callable[[str], None]] = None,
) -> list[ReconciliationResult]:
    """跑一批 check, 返回所有 result.

    Args:
        checks: list of ReconciliationCheck 实例
        on_progress: 可选回调, 跑每个 check 前调 on_progress(check.name)

    Returns:
        list[ReconciliationResult] (跟 checks 同顺序)
    """
    results = []
    for ck in checks:
        if on_progress:
            on_progress(ck.name)
        results.append(ck.run())
    return results


def classify_money_severity(
    abs_delta: float,
    base: float,
    *,
    negligible_abs: float = 1.0,
    negligible_rel: float = 0.0001,
    review_abs: float = 100.0,
    review_rel: float = 0.001,
    fatal_rel: float = 0.01,
) -> ReconciliationSeverity:
    """金额对账的通用 severity 分类.

    NEGLIGIBLE:   |abs| < negligible_abs 或 (rel < negligible_rel)
    MUST_FIX:     rel > fatal_rel
    NEEDS_REVIEW: 其它
    """
    abs_d = abs(abs_delta)
    rel = (abs_d / abs(base)) if base else 0.0
    if abs_d < negligible_abs or rel < negligible_rel:
        return ReconciliationSeverity.NEGLIGIBLE
    if rel > fatal_rel:
        return ReconciliationSeverity.MUST_FIX
    if abs_d > review_abs or rel > review_rel:
        return ReconciliationSeverity.NEEDS_REVIEW
    return ReconciliationSeverity.NEGLIGIBLE
