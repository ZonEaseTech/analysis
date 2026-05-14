"""Internal consistency check — 跑 FULL_IDENTITIES 校验器, 包装成 Reconciliation Result.

这是个把 row-level validator 提升到 cross-system reconciliation 视角的"桥".
让"所有内部对账 + 跨系统对账"通过同一个接口跑 + 同一个 Result schema 报告.

实际工作:
  1. 接收 check_rows (已聚合好的 P&L 行或 SKU 行)
  2. 跑 FULL_IDENTITIES (DEFAULT + SOURCE_COVERAGE + SANITY_BAND)
  3. 把 validator.Violation 转 reconciliation.Discrepancy
  4. 汇总 severity (跑出 MUST_FIX 就是 MUST_FIX)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from semantic.validators import check
from semantic.validators.core import Severity
from semantic.validators.identities import FULL_IDENTITIES

from ..base import (
    Discrepancy,
    ReconciliationResult,
    ReconciliationSeverity,
)


# Severity 映射: validators.Severity → reconciliation.ReconciliationSeverity
_SEVERITY_MAP = {
    Severity.NEGLIGIBLE:    ReconciliationSeverity.NEGLIGIBLE,
    Severity.NEEDS_REVIEW:  ReconciliationSeverity.NEEDS_REVIEW,
    Severity.MUST_FIX:      ReconciliationSeverity.MUST_FIX,
}


@dataclass
class InternalConsistencyCheck:
    """跑 FULL_IDENTITIES (恒等式 + source coverage + sanity bands) 当对账 check.

    用法:
        check = InternalConsistencyCheck(
            name="profit_margin 2026-04",
            rows=check_rows,
        )
        result = check.run()
        if result.has_must_fix: ...

    `identities` 默认走 FULL_IDENTITIES, 也可以传子集 (e.g. 只跑 SANITY_BAND).
    """

    name: str
    rows: Iterable[dict]
    identities: Optional[list] = None
    row_label: Optional[Callable[[dict], str]] = None

    def run(self) -> ReconciliationResult:
        identities = self.identities or FULL_IDENTITIES
        validator_result = check(list(self.rows), identities)

        discrepancies = []
        max_sev = ReconciliationSeverity.NEGLIGIBLE
        for v in validator_result.violations:
            entity_id = (
                self.row_label(v.row) if self.row_label
                else f"{v.row.get('store_num','?')}/{v.row.get('item_name','?')}"
            )
            recon_sev = _SEVERITY_MAP.get(
                v.severity, ReconciliationSeverity.NEGLIGIBLE
            )
            if int(recon_sev) > int(max_sev):
                max_sev = recon_sev
            discrepancies.append(Discrepancy(
                entity_id=f"{v.identity.name} @ {entity_id}",
                bq_value=v.lhs,
                external_value=v.rhs,
                note=f"identity={v.identity.name}, severity={v.severity.name}",
            ))

        summary = (
            f"{validator_result.total_rows} rows × {len(identities)} identities"
            f" → {len(discrepancies)} violations"
            f" (max severity: {max_sev.name})"
        )

        return ReconciliationResult(
            check_name=self.name,
            total_compared=validator_result.total_rows * len(identities),
            discrepancies=discrepancies,
            severity=max_sev,
            summary=summary,
        )
