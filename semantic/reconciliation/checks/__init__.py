"""Reconciliation checks — 具体的对账 check 实现集合.

Phase 1 落地 (本):
  - internal_consistency: 内部数学恒等式对账 (复用 validator 三级 severity)

Phase 2 待接入数据后:
  - ttpos_anchor:      BQ vs ttpos CountSale.TotalReceivedAmount (口径对账锚)
  - ttpos_cost_anchor: 物料单价对账锚 — 我们管线 vs ERP 按 ttpos 算法复算真值
  - erpnext_consumption: BQ 物料消耗 vs ERPNext 出库 (BOM 准确性)
  - platform_payout:   BQ 外卖营收 vs 平台对账单 (Grab/LINE MAN)
"""
from .internal_consistency import InternalConsistencyCheck
from .platform_payout import (
    PlatformPayoutCheck,
    PlatformPayoutRecord,
    load_grab_statement,
    load_lineman_statement,
    load_shopee_statement,
)
from .ttpos_anchor import TtposAnchorCheck, TTPOS_NET_SALES_SQL
from .ttpos_cost_anchor import (
    TtposCostAnchorResult,
    compute_ttpos_unit_cost,
    fetch_ttpos_truths_from_erp,
    run_cost_anchor,
)

__all__ = [
    "InternalConsistencyCheck",
    "PlatformPayoutCheck",
    "PlatformPayoutRecord",
    "TtposAnchorCheck",
    "TTPOS_NET_SALES_SQL",
    "TtposCostAnchorResult",
    "compute_ttpos_unit_cost",
    "fetch_ttpos_truths_from_erp",
    "run_cost_anchor",
    "load_grab_statement",
    "load_lineman_statement",
    "load_shopee_statement",
]
