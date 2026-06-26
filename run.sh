#!/bin/bash
# 快速运行分析脚本的快捷方式。
# 用法: ./run.sh <脚本路径> [参数]
#   例: ./run.sh bq_reports/pnl_statement.py 2026-06
#       ./run.sh scripts/adhoc/business_summary.py

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ $# -eq 0 ]; then
    echo "用法: ./run.sh <脚本路径> [参数]"
    echo ""
    echo "常用入口:"
    echo "  bq_reports/profit_margin_report.py    — 中间表(对账锚)"
    echo "  bq_reports/profit_by_price_report.py   — 客户交付物"
    echo "  bq_reports/pnl_statement.py            — 全面 P&L"
    echo "  scripts/adhoc/business_summary.py      — 营业数据汇总"
    echo "  scripts/adhoc/recon_cost_vs_summary_bridge.py — 成本/汇总对账桥"
    exit 1
fi

venv/bin/python "$@"
