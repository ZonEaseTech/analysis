#!/usr/bin/env python3
"""
订单国籍报表 - BigQuery 版本

功能: 导出单门店订单国籍明细和汇总

Usage:
    python -m bq_reports.report_orders_by_nationality_bq --output exports/orders_nationality.xlsx
"""

import argparse
import sys

from .utils.bq_exporter import ReportExporter
from .utils.bq_client import setup_proxy


def main():
    parser = argparse.ArgumentParser(description="订单国籍报表导出")
    parser.add_argument("--output", required=True, help="输出 Excel 文件路径")
    parser.add_argument("--project", default="diyl-407103", help="GCP 项目 ID")

    args = parser.parse_args()

    print("开始导出订单国籍报表...")

    setup_proxy()

    exporter = ReportExporter(
        project_id=args.project,
        output_path=args.output
    )

    result = exporter.export_orders_by_nationality(
        output_path=args.output
    )

    print(f"\n导出完成!")
    print(f"  输出文件: {result.output_path}")
    print(f"  Sheet1: 订单明细")
    print(f"  Sheet2: 国籍汇总")

    # 打印校验结果
    if result.validation_result:
        status = "通过" if result.validation_result.is_valid else "失败"
        print(f"\n数据校验: {status}")
        if result.validation_result.errors:
            print(f"  错误 ({len(result.validation_result.errors)} 条):")
            for err in result.validation_result.errors:
                print(f"    - [{err.rule}] {err.message}")
        if result.validation_result.warnings:
            print(f"  警告 ({len(result.validation_result.warnings)} 条):")
            for warn in result.validation_result.warnings:
                print(f"    - [{warn.rule}] {warn.message}")
        print(f"  统计: {result.validation_result.stats}")

    return 0 if (result.validation_result is None or result.validation_result.is_valid) else 1


if __name__ == "__main__":
    sys.exit(main())
