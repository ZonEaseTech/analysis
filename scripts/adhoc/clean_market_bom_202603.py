#!/usr/bin/env python3
"""把 2026-05-14 市场提供的混乱 Sheet2 BOM 清洗成标准 fallback BOM 格式.

来源:
  /workspace/data/uploads/01KRJ7Q7GEN0MMWE9DEGK58PN5.xlsx
  (= profit_by_price_202603_v14-补充BOM.xlsx, 市场基于 Sheet1 手写意图加工出的
   带物料编码完整 BOM)
  Sheet2 含 3 种列偏移变体 (148 行主格式 + 9 行 cidx=18 + 2 行 cidx=19),
  商品名只在 BOM 首行出现, 后续承接上一商品.

输出: resources/wallace.20260514/profit_by_price_202603_v14-补充BOM.csv
  - 清洗后归档成 CSV (不是 xlsx): 纯文本能 git diff, 读取快, 体积小
  - 原始 xlsx 留本地不进 git (体积大); 清洗后 CSV 进 git (config 引用)
  - 全部商品都保留 (Sheet1 单品 + 套餐), 含促销/旧款商品 — 市场明确要补的不删.

⚠️ 重要: 本层在 config 里必须配 match_mode: exact.
  否则 5 层模糊匹配会让普通"鸡块"前缀命中"鸡块（电影票折扣）"(物料是薯条+鸡柳).
  补充 BOM 的 key 是市场从报表 copy 的精确商品名, 本就该精确匹配.

一次性脚本, 跑完归档, 不进生产链路。
"""
from __future__ import annotations

import csv
import re
import openpyxl

# RAW = 归档的客户原始文件 (从 /workspace/data/uploads/ 的 UUID 名 cp 过来),
# 改读归档位置使脚本可重跑. 客户给新版时覆盖这个文件再跑一次即可.
RAW = "resources/wallace.20260514/profit_by_price_202603_v14-补充BOM_原始.xlsx"
OUT = "resources/wallace.20260514/profit_by_price_202603_v14-补充BOM.csv"

CODE_RE = re.compile(r"^[A-Z]{2}\d{5}$")


def find_code_col(row):
    for j, v in enumerate(row):
        if isinstance(v, str) and CODE_RE.match(v):
            return j
    return None


def main():
    wb = openpyxl.load_workbook(RAW, data_only=True, read_only=True)
    ws = wb["Sheet2"]

    records = []
    current_product = None
    skipped = []

    for i, r in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        if r[5] and isinstance(r[5], str) and r[5].strip():
            current_product = r[5].strip()
        cidx = find_code_col(r)
        if cidx is None:
            continue
        if current_product is None:
            skipped.append((i + 1, "无 carry-forward product"))
            continue
        code = r[cidx]
        mat_name = r[cidx - 1] if cidx > 0 and isinstance(r[cidx - 1], str) else None
        qty = r[cidx + 1] if cidx + 1 < len(r) else None
        # cidx+2 是单价 (跟 itemcode 表派生), 不取 — 单价走 material_price_sources
        unit = r[cidx + 3] if cidx + 3 < len(r) else None
        if not isinstance(qty, (int, float)):
            skipped.append((i + 1, f"qty 非数字: {qty}"))
            continue
        records.append((current_product, code, mat_name, float(qty), unit))

    print(f"清洗: {len(records)} 条 BOM, 跳过 {len(skipped)} 行")
    products = sorted({r[0] for r in records})
    print(f"涉及 {len(products)} 个商品:")
    for p in products:
        print(f"  • {p}")

    # 输出 CSV: utf-8-sig 让 Excel 双击不乱码; lineterminator='\n' 统一 LF 行尾
    # (csv.writer 默认 \r\n, 跟 git 仓库 LF 惯例冲突会让 diff 全文件标变更)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["商品名称", "物品编号", "物品名称", "单耗", "单位"])
        for r in records:
            w.writerow(list(r))
    print(f"已保存: {OUT}")


if __name__ == "__main__":
    main()
