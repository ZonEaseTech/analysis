#!/usr/bin/env python3
"""
销售业绩 + 物品消耗报表 - BigQuery 版本

功能: 统计月度销售业绩（总营业额、实收金额、订单数）和物品消耗明细

Usage:
    python -m bq_reports.report_sales_consumption_bq --month 2026-01 --output exports/sales_2026_01.xlsx
"""

import argparse
import sys

from .utils.bq_exporter import ReportExporter
from semantic.dimensions.time import assert_month_not_frozen


def main():
    parser = argparse.ArgumentParser(description="销售业绩 + 物品消耗报表导出")
    parser.add_argument("--month", required=True, help="月份，格式 YYYY-MM，如 2026-01")
    parser.add_argument("--merchants", default="resources/merchants.xlsx",
                        help="商家列表 Excel 路径")
    parser.add_argument("--output", required=True, help="输出 Excel 文件路径")
    parser.add_argument("--project", default="diyl-407103", help="GCP 项目 ID")
    parser.add_argument("--external", default=None,
                        help="外部销售源, 格式 'provider:key=val', e.g. 'huku:path=/path/to/file.xlsx'")
    parser.add_argument("--force", action="store_true",
                        help="校验失败仍强制导出 (文件带水印, 不得对外交付)")

    args = parser.parse_args()

    assert_month_not_frozen(args.month)
    print(f"开始导出 {args.month} 月的销售业绩和物品消耗报表...")

    exporter = ReportExporter(
        project_id=args.project,
        output_path=args.output
    )

    from semantic.validators.gate import GateSpec

    exporter.set_gate(GateSpec(
        identities=[],  # 技术债: 该报表字段未对齐销售恒等式, 暂只做非空闸门; Task 11 基线工厂落地后升级必填字段校验
        force=args.force,
        report_name="sales_consumption",
        build_check_rows=lambda rows: [{"_row": i} for i, _ in enumerate(rows)],
    ))

    result = exporter.export_sales_and_consumption(
        month=args.month,
        merchant_xlsx=args.merchants,
        output_path=args.output,
        external_spec=args.external,
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
