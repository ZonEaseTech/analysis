"""PlatformPayoutCheck — 跟 Grab/LINE MAN/Shopee 月度对账单对账 (P4 Phase 2).

价值: 这是**最关键的对账锚** — BQ 算的外卖收入 vs **商家银行账户实际收到的钱**.
TtposAnchorCheck 只能验证"跟 ttpos 后端一致", PlatformPayoutCheck 才能验证"跟
真实钱流一致". 业界叫 "true reconciliation" 或 "settlement tie-out".

数据源 (待客户提供):
  - Grab 商家后台 → 月度对账单 Excel/CSV
  - LINE MAN 商家后台 → 同上
  - Shopee Mall 商家后台 → 同上
  - 每月人工下载 / 后续可对接平台 API

对账维度:
  - 平台 × 期间 (Grab / LINEMAN / Shopee, 月度)
  - 平台 × 店 (粒度更细; 取决于对账单是否按店明细)

关键字段映射 (跟 utils/resource_adapter 对账单 adapter 一致):
  Grab CSV → {merchant_id, period_start, period_end, gross_sales, commission,
               adjustments, net_payout}
  LINE MAN → 类似
  Shopee   → 类似

实施状态:
  ✅ Phase 1 (本): Check + Spec + Excel adapter 占位
  ⏳ Phase 2 (后续, 等客户给样本): 平台特定 adapter 实现 +
                                  实际跑 BQ vs 对账单数字
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..base import (
    Discrepancy,
    ReconciliationResult,
    ReconciliationSeverity,
    classify_money_severity,
)


@dataclass(frozen=True)
class PlatformPayoutRecord:
    """一条平台对账单记录 (一店 × 一月度 × 一平台)."""

    platform: str            # "grab" | "lineman" | "shopee"
    period: str              # "2026-04"
    store_id: str            # 跟 ttpos 的店 uuid 关联
    gross_sales: float       # 平台显示的销售总额 (跟 BQ 外卖营收对账)
    commission: float        # 平台抽佣 (替代估算)
    adjustments: float = 0.0  # 调整项 (促销补贴 / 罚款 / 退款)
    net_payout: float = 0.0   # 平台实际打到商家银行的钱
    raw_source: str = ""      # 原始文件 (审计追溯)


@dataclass
class PlatformPayoutCheck:
    """对账 Check: BQ 外卖营收 vs 平台对账单 gross_sales.

    用法 (Phase 2 拿到对账单后):
        records = load_grab_statement("path/to/grab_202604.xlsx")
        ck = PlatformPayoutCheck(
            name="Grab Payout 2026-04",
            bq_by_store={"shop_001": 145000.0, ...},  # BQ 外卖营收 by store
            payout_records=records,
        )
        result = ck.run()

    Severity (商家结算对账, 严格度跟 TtposAnchor 一致):
      < 1 元                    → NEGLIGIBLE
      < 100 元 / 0.1%           → NEEDS_REVIEW
      > 0.5% (商家结算容差)      → MUST_FIX
    """

    name: str
    bq_by_store: dict       # {store_id: bq_takeout_net_sales}
    payout_records: list    # list[PlatformPayoutRecord]
    platform_filter: str = ""  # 空 = 全部平台; 否则只跑该平台

    def run(self) -> ReconciliationResult:
        records = self.payout_records
        if self.platform_filter:
            records = [r for r in records if r.platform == self.platform_filter]

        discrepancies = []
        max_sev = ReconciliationSeverity.NEGLIGIBLE
        total_compared = 0

        for record in records:
            bq_value = float(self.bq_by_store.get(record.store_id, 0))
            delta = bq_value - record.gross_sales
            sev = classify_money_severity(
                abs_delta=delta, base=record.gross_sales,
                negligible_abs=1.0,
                review_abs=100.0,
                fatal_rel=0.005,   # 0.5% 容差 (比 ttpos anchor 宽, 因为对账单
                                    # 跟 BQ 数据采集时刻不同步)
            )
            total_compared += 1
            if int(sev) > int(max_sev):
                max_sev = sev
            if sev != ReconciliationSeverity.NEGLIGIBLE:
                discrepancies.append(Discrepancy(
                    entity_id=f"{record.platform}/{record.store_id}/{record.period}",
                    bq_value=bq_value,
                    external_value=record.gross_sales,
                    note=(
                        f"平台对账单 vs BQ 外卖营收\n"
                        f"  raw_source: {record.raw_source}\n"
                        f"  commission: {record.commission:,.2f}, "
                        f"adjustments: {record.adjustments:,.2f}, "
                        f"net_payout: {record.net_payout:,.2f}"
                    ),
                ))

        return ReconciliationResult(
            check_name=self.name,
            total_compared=total_compared,
            discrepancies=discrepancies,
            severity=max_sev,
            summary=(
                f"{total_compared} 对账单记录, "
                f"{total_compared - len(discrepancies)} OK, "
                f"{len(discrepancies)} 差异, "
                f"最高 severity: {max_sev.name}"
            ),
        )


# ───────────────────────────────────────────────────────────────
# 加载器占位 (待 Phase 2 拿到对账单样本后实施)
# ───────────────────────────────────────────────────────────────

def load_grab_statement(path: str) -> list[PlatformPayoutRecord]:
    """加载 Grab 月度对账单 Excel/CSV → PlatformPayoutRecord 列表.

    实际字段映射待 Grab 商家后台对账单样本确认. 典型字段 (基于公开文档):
      - Period Start / Period End
      - Merchant ID
      - Gross Sales (商家显示销售总额)
      - Commission (平台抽佣)
      - Adjustments (促销补贴 / 退款 / 罚款合计)
      - Net Payout (实际打款)

    Phase 2 实施清单:
      1. 拿一份 Grab 对账单样本 → 看 Excel 真实列名
      2. 写 mapping (excel column → PlatformPayoutRecord field)
      3. 实现本函数
    """
    raise NotImplementedError(
        "load_grab_statement: 待 Phase 2 拿到 Grab 对账单样本后实施. "
        "样本格式见 utils/resource_adapter 文档."
    )


def load_lineman_statement(path: str) -> list[PlatformPayoutRecord]:
    """加载 LINE MAN 月度对账单. 实施同 load_grab_statement."""
    raise NotImplementedError(
        "load_lineman_statement: 待 Phase 2 拿到 LINE MAN 对账单样本后实施."
    )


def load_shopee_statement(path: str) -> list[PlatformPayoutRecord]:
    """加载 Shopee Food 月度对账单."""
    raise NotImplementedError(
        "load_shopee_statement: 待 Phase 2 拿到 Shopee 对账单样本后实施."
    )
