#!/usr/bin/env python3
"""成本表 vs 汇总表 支付对账桥 (thin wrapper).

核心逻辑已提取到 semantic/reconciliation/checks/payment_bridge.py。
本脚本保留逐店残差打印, 方便 adhoc 排查。

用法: venv/bin/python scripts/adhoc/recon_cost_vs_summary_bridge.py 2026-05
"""
import sys, csv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID
from semantic.dimensions.time import month_to_ts_range
from semantic.reconciliation.checks.payment_bridge import PaymentBridgeCheck

month = sys.argv[1] if len(sys.argv) > 1 else '2026-05'
s, e = month_to_ts_range(month)
setup_proxy(); client = get_bq_client()
uuids = [r['商家ID'].strip() for r in csv.DictReader(
    open('resources/wallace.20260525/华莱士商家60家ID.csv', encoding='utf-8-sig'))]
datasets = [f"shop{u}" for u in uuids]

check = PaymentBridgeCheck(client, PROJECT_ID, datasets, s, e)
results = check.run()
check.print_summary(results)
# 逐店残差 top5 (防正负抵消)
top5 = sorted(results, key=lambda r: abs(r.residue), reverse=True)[:5]
print("\n逐店残差 |绝对值| top5:")
for r in top5:
    print(f"  {r.store_id[-8:]}  残差={r.residue:>10,.2f}")
abs_sum = sum(abs(r.residue) for r in results)
print(f"逐店残差绝对值合计 = {abs_sum:,.2f} (净残差 {check.aggregate(results).residue:,.2f})")
