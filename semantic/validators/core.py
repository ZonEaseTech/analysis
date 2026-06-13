"""Identity + Severity + check() — the validator kernel.

Reads `list[dict]` rows + `list[Identity]` rules → produces a `Result`
that callers print via `print_result()` or inspect programmatically.

Design points worth knowing before changing:

  - `Identity.classify` is a **function**, not a fixed threshold. This lets
    each identity define its own tolerance (integer identities want 0;
    money identities want abs + relative floors). dbt's warn_if/error_if
    is a special case of this pattern.
  - `Result.violations` is a flat list of `(identity, row, lhs, rhs, severity)`
    — easy to slice by severity for printing or filtering.
  - Identifying info on a row (store/sku) is up to the caller's `row_label`
    callback; the validator doesn't know about your row schema beyond the
    keys the identity references.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable


class Severity(IntEnum):
    """Tiered severity, matches Soda Checks' pass/warn/fail."""

    NEGLIGIBLE = 0    # 浮点/累积误差 — 自动忽略，不打扰
    NEEDS_REVIEW = 1  # 🟡 可接受范围但要人看一眼
    MUST_FIX = 2      # 🔴 离谱，必须排查


@dataclass(frozen=True)
class Identity:
    """One reconciliation rule: lhs(row) should equal rhs(row).

    The `classify(delta, lhs)` callback decides severity from the residual.
    Different identities have different naturalness — integers should never
    drift, money rounds at the second decimal.
    """

    name: str
    lhs: Callable[[dict], float]
    rhs: Callable[[dict], float]
    classify: Callable[[float, float], Severity]
    description: str = ""
    # 该恒等式读取的 row 字段清单 — 扰动测试 (tests/test_identity_perturbation)
    # 据此逐字段扰动验证可证伪性. lambda 无法内省, 故显式声明.
    fields: tuple[str, ...] = ()


@dataclass
class Violation:
    identity: Identity
    row: dict
    lhs: float
    rhs: float
    severity: Severity

    @property
    def delta(self) -> float:
        return self.lhs - self.rhs


@dataclass
class Result:
    """Outcome of one `check()` run."""

    total_rows: int
    identities: list[Identity]
    # Flat list, easier to group/filter by severity than nested dicts.
    violations: list[Violation] = field(default_factory=list)

    def by_severity(self, sev: Severity) -> list[Violation]:
        return [v for v in self.violations if v.severity == sev]

    def by_identity(self, identity_name: str) -> list[Violation]:
        return [v for v in self.violations if v.identity.name == identity_name]

    @property
    def has_must_fix(self) -> bool:
        return any(v.severity == Severity.MUST_FIX for v in self.violations)


def check(rows: list[dict], identities: list[Identity]) -> Result:
    """Apply every identity to every row, collect non-NEGLIGIBLE violations.

    Skips rows where lhs/rhs raise (e.g. missing fields) — they're surfaced
    as identity-wide errors instead. Defensive because aggregate data shape
    drifts more often than report code.
    """
    result = Result(total_rows=len(rows), identities=list(identities))
    for row in rows:
        for ident in identities:
            try:
                lhs = float(ident.lhs(row))
                rhs = float(ident.rhs(row))
            except (KeyError, TypeError, ValueError):
                # Treat schema errors as MUST_FIX with sentinel values, so
                # the user sees them prominently instead of silently passing.
                result.violations.append(Violation(ident, row, 0.0, 0.0, Severity.MUST_FIX))
                continue
            sev = ident.classify(lhs - rhs, lhs)
            if sev != Severity.NEGLIGIBLE:
                result.violations.append(Violation(ident, row, lhs, rhs, sev))
    return result


def print_result(
    result: Result,
    *,
    row_label: Callable[[dict], str] = lambda r: str(r),
    top_n_review: int = 10,
) -> None:
    """Pretty-print the result to console.

    - 🔴 MUST_FIX: list ALL (you don't want to miss any critical).
    - 🟡 NEEDS_REVIEW: list top-N by |delta|, then "…还有 X 条略".
    - ✅ NEGLIGIBLE + pass: counts only.
    """
    for ident in result.identities:
        viols = result.by_identity(ident.name)
        must = [v for v in viols if v.severity == Severity.MUST_FIX]
        review = [v for v in viols if v.severity == Severity.NEEDS_REVIEW]
        passed = result.total_rows - len(must) - len(review)
        print(f"[校验] {ident.name}")
        if ident.description:
            print(f"  {ident.description}")
        print(f"  ✅ {passed:>5} 通过")
        if review:
            print(f"  🟡 {len(review):>5} 需复核")
        if must:
            print(f"  🔴 {len(must):>5} 离谱")
        print()

        if must:
            print(f"  🔴 离谱清单（全部 {len(must)} 条）：")
            _print_violations(sorted(must, key=lambda v: -abs(v.delta)), row_label)
            print()

        if review:
            shown = sorted(review, key=lambda v: -abs(v.delta))[:top_n_review]
            print(f"  🟡 复核清单（top {len(shown)} by |delta|，共 {len(review)} 条）：")
            _print_violations(shown, row_label)
            if len(review) > top_n_review:
                print(f"  ⏬ 还有 {len(review) - top_n_review} 条略")
            print()


def _print_violations(viols: list[Violation], row_label: Callable[[dict], str]) -> None:
    for v in viols:
        rel = (abs(v.delta) / abs(v.lhs) * 100) if v.lhs else 0
        line = (f"    {row_label(v.row):<40}  "
                f"LHS {v.lhs:>10,.2f}  RHS {v.rhs:>10,.2f}  "
                f"delta {v.delta:>+10,.2f} ({rel:>5.1f}%)")
        # P2: 附加 source 元数据 (如果 row 有 bom_source / price_source).
        # 让 console 直接显示"差额来自哪份数据源", 不用再去 Excel 翻审计列.
        srcs = []
        if "bom_source" in v.row:
            srcs.append(f"bom={v.row['bom_source']}")
        if "price_source" in v.row:
            srcs.append(f"price={v.row['price_source']}")
        if srcs:
            line += "  [" + " | ".join(srcs) + "]"
        print(line)
