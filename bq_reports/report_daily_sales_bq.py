#!/usr/bin/env python3
"""
单品日销量报表 - BigQuery 版本

对应原脚本: ../ttpos-n8n-scheduler/scripts/report_wallace_daily_item_sales.sh
功能: 堂食 + 外卖单品日销量明细

Usage:
    python scripts/report_daily_sales_bq.py --month 2026-01 --output exports/daily_sales_2026_01.xlsx
"""

import argparse
import sys

from .utils.bq_exporter import ReportExporter


def main():
    parser = argparse.ArgumentParser(description="单品日销量报表导出")
    parser.add_argument("--month", required=True, help="月份，格式 YYYY-MM，如 2026-01")
    parser.add_argument("--merchants", default="resources/merchants.xlsx",
                        help="商家列表 Excel 路径")
    parser.add_argument("--output", required=True, help="输出 Excel 文件路径")
    parser.add_argument("--project", default="diyl-407103", help="GCP 项目 ID")
    parser.add_argument("--force", action="store_true",
                        help="校验失败仍强制导出 (文件带水印, 不得对外交付)")

    args = parser.parse_args()

    print(f"开始导出 {args.month} 月的单品日销量报表...")

    exporter = ReportExporter(
        project_id=args.project,
        output_path=args.output
    )

    from semantic.validators.gate import GateSpec

    exporter.set_gate(GateSpec(
        identities=[],  # 技术债: 该报表字段未对齐销售恒等式, 暂只做非空闸门; Task 11 基线工厂落地后升级必填字段校验
        force=args.force,
        report_name="daily_sales",
        build_check_rows=lambda rows: [{"_row": i} for i, _ in enumerate(rows)],
    ))

    result = exporter.export_daily_item_sales(
        month=args.month,
        merchant_xlsx=args.merchants,
        output_path=args.output
    )
    
    print(f"\n导出完成!")
    print(f"  总门店数: {result.total_count}")
    print(f"  成功: {result.success_count}")
    print(f"  失败: {result.failed_count}")
    print(f"  输出文件: {result.output_path}")
    
    if result.errors:
        print(f"\n错误详情:")
        for err in result.errors[:5]:
            print(f"  - {err}")
        if len(result.errors) > 5:
            print(f"  ... 还有 {len(result.errors) - 5} 个错误")
    
    return 0 if result.failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
