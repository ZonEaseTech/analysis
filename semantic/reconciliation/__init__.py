"""Cross-system reconciliation layer — 跨系统对账 (P4).

业界金标准之一. 从"内部自洽"升级到"跟外部系统对得上":
  - BQ 计算 vs ttpos 后台 (Net Sales 锚)
  - BQ 计算 vs ERPNext 物料出库 (BOM 准确性验证)
  - BQ 外卖营收 vs 平台对账单 (Grab/LINE MAN/Shopee, 待 Phase 2 数据接入)
  - BQ 物料消耗 vs ERP 出库 (套餐 BOM 准确性)

设计:
  Check 协议 - 一个 Check 跑一次返回 ReconciliationResult.
  数据源 (sources/) 跟 check 解耦, 让 source 跨多个 check 复用.
  Result 含 severity 三级 (跟 validators 一致), 让上层统一处理.
"""
from .base import (
    Discrepancy,
    ReconciliationCheck,
    ReconciliationResult,
    ReconciliationSeverity,
    run_checks,
)
from .checks.internal_consistency import InternalConsistencyCheck
from .checks.platform_payout import (
    PlatformPayoutCheck,
    PlatformPayoutRecord,
    load_grab_statement,
    load_lineman_statement,
    load_shopee_statement,
)
from .checks.ttpos_anchor import TtposAnchorCheck, TTPOS_NET_SALES_SQL

__all__ = [
    "Discrepancy",
    "ReconciliationCheck",
    "ReconciliationResult",
    "ReconciliationSeverity",
    "run_checks",
    "InternalConsistencyCheck",
    "PlatformPayoutCheck",
    "PlatformPayoutRecord",
    "TtposAnchorCheck",
    "TTPOS_NET_SALES_SQL",
    "load_grab_statement",
    "load_lineman_statement",
    "load_shopee_statement",
]
