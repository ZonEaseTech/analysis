#!/usr/bin/env python3
"""接入 2026-05-14 市场"最终 BOM和价格"事实表 — 清洗 + 生成差异报告.

来源: resources/wallace.20260514/最终BOM和价格_原始.xlsx (市场上传, 留本地不进 git)
  3 sheet: 单品 BOM / 套餐 BOM / 价格
  文件按门店重复了 ~51 次, 但重复行消耗数量完全一致 → dedup 安全.

输出 (进 git, config 引用):
  resources/wallace.20260514/最终BOM_202605.csv     单品+套餐合并 dedup, 262 商品
  resources/wallace.20260514/最终价格_202605.csv    102 物料 code

差异报告 (给用户 review, 不进 git):
  exports/diff_最终BOM价格_vs_现有.xlsx
    - 商品覆盖: 新增 / 丢失 / 重叠
    - BOM 内容变动: 重叠商品的物料增删 + 消耗量变化
    - 价格变动: code 逐行 old→new

"以最新为准" — 新文件是成套替换现有 3 层 BOM + 2 层价格的权威来源.

一次性脚本, 跑完归档.
"""
from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import openpyxl

RAW = "resources/wallace.20260514/最终BOM和价格_原始.xlsx"
OUT_BOM = "resources/wallace.20260514/最终BOM_202605.csv"
OUT_PRICE = "resources/wallace.20260514/最终价格_202605.csv"
DIFF_XLSX = "exports/diff_最终BOM价格_vs_现有.xlsx"

# POS 简称 → BOM 文件里全称的别名映射 (5/18 跟市场对齐结果).
# 这些 SKU 在销售里用简称, BOM 文件里只有全称, fuzzy 匹不上.
# 清洗时把全称那行 BOM 复制一份, 以简称为 key 落 csv.
# 新版 BOM 文件来了重跑本脚本, alias 自动保留.
PRODUCT_ALIASES = {
    "辣番茄2": "辣番茄套餐 2",
    "合艾狂热1": "合艾狂热 1",           # ⚠️ BOM 漏录, 待市场补 → 当前 alias 不生效, 仍标"无"
    "合艾狂热2": "合艾狂热 2",           # POS 简称版 future-proof (当前销售未出现)
    "新店开业套餐1": "新店开业促销，满意十足，10%特别折扣",
    "冰淇淋 0泰铢 - Google评论": "冰淇淋 0฿",
    "套餐满足1经典  旧": "套餐满足1经典 旧",   # 双空格 vs 单空格
}


def load_new():
    """读原始 xlsx → (bom_dict, price_dict).

    bom_dict: {商品: [(code, name, qty, unit), ...]}  (carry-forward + dedup)
    price_dict: {code: (name, price, unit)}
    """
    wb = openpyxl.load_workbook(RAW, data_only=True)
    bom = {}
    for sn in ("单品", "套餐"):
        ws = wb[sn]
        cur = None
        for i, r in enumerate(ws.iter_rows(values_only=True)):
            if i == 0 or not any(c is not None for c in r):
                continue
            if r[0]:
                cur = str(r[0]).strip()
                bom.setdefault(cur, [])
            if cur and r[2]:
                code = str(r[2]).strip()
                if code in {x[0] for x in bom[cur]}:
                    continue  # dedup (按门店重复, 已验证消耗量一致)
                bom[cur].append((code, str(r[1] or "").strip(),
                                 float(r[3] or 0), str(r[4] or "").strip()))
    price = {}
    ws = wb["价格"]
    for i, r in enumerate(ws.iter_rows(values_only=True)):
        if i == 0 or not r[1]:
            continue
        code = str(r[1]).strip()
        if code and code not in price:
            price[code] = (str(r[0] or "").strip(), r[2], str(r[3] or "").strip())

    # 应用 POS 简称别名 — 复制全称行到简称 key
    missing = []
    for alias, target in PRODUCT_ALIASES.items():
        if target in bom and alias not in bom:
            bom[alias] = list(bom[target])
        elif target not in bom:
            missing.append((alias, target))
    if missing:
        print(f"⚠️ alias 目标在 BOM 中找不到 (skip): {missing}")
    return bom, price


def write_csvs(bom, price):
    with open(OUT_BOM, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["商品名称", "物品编号", "物品名称", "单耗", "单位"])
        for prod, recs in bom.items():
            for code, name, qty, unit in recs:
                w.writerow([prod, code, name, qty, unit])
    with open(OUT_PRICE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["物品编号", "物品名称", "物料单价", "单位"])
        for code, (name, p, unit) in price.items():
            w.writerow([code, name, p, unit])
    print(f"清洗输出: {OUT_BOM} ({sum(len(v) for v in bom.values())} 行 / {len(bom)} 商品)")
    print(f"          {OUT_PRICE} ({len(price)} code)")


def build_diff(bom, price):
    from bq_reports.profit_margin_report import (
        load_config, _load_bom_layers, _match_bom_layered,
        _load_material_price_layers, _build_material_price_resolver)

    cfg = load_config()
    layers = _load_bom_layers(cfg)
    price_layers = _load_material_price_layers(cfg)
    resolver = _build_material_price_resolver({}, {}, price_layers, strict=True)

    existing_products = set()
    for _n, _p, boms, _m in layers:
        existing_products |= set(boms.keys())

    wb = openpyxl.Workbook()

    # Sheet 1: 商品覆盖
    ws1 = wb.active
    ws1.title = "商品覆盖"
    ws1.append(["商品名称", "状态"])
    new_set = set(bom)
    for p in sorted(new_set - existing_products):
        ws1.append([p, "新增 (现有没有)"])
    for p in sorted(existing_products - new_set):
        ws1.append([p, "丢失 (替换后无 BOM)"])
    for p in sorted(new_set & existing_products):
        ws1.append([p, "重叠"])

    # Sheet 2: BOM 内容变动
    ws2 = wb.create_sheet("BOM内容变动")
    ws2.append(["商品名称", "现有来源", "新增物料", "删除物料", "消耗量变化"])
    for prod, recs in bom.items():
        matched, src = _match_bom_layered(prod, layers)
        if not matched:
            continue
        old = {c: q for c, _n, q, _u in matched}
        new = {c: q for c, _n, q, _u in recs}
        added = set(new) - set(old)
        removed = set(old) - set(new)
        qty_chg = {c: (old[c], new[c]) for c in set(old) & set(new)
                   if abs(float(old[c] or 0) - float(new[c] or 0)) > 1e-6}
        if added or removed or qty_chg:
            ws2.append([
                prod, src,
                ", ".join(sorted(added)) or "-",
                ", ".join(sorted(removed)) or "-",
                "; ".join(f"{c}: {o}→{n}" for c, (o, n) in qty_chg.items()) or "-",
            ])

    # Sheet 3: 价格变动
    ws3 = wb.create_sheet("价格变动")
    ws3.append(["物品编号", "物品名称", "现有价", "新文件价", "倍数", "类型"])
    for code, (name, np, _unit) in price.items():
        r = resolver.resolve((code, name))
        if r is None:
            ws3.append([code, name, "-", np, "-", "新增 (现有没有)"])
        elif abs(float(r.value or 0) - float(np or 0)) > 1e-6:
            old_v = float(r.value or 0)
            mult = f"{float(np or 0) / old_v:.1f}×" if old_v else "-"
            ws3.append([code, name, r.value, np, mult, "变动"])
    wb.save(DIFF_XLSX)
    print(f"差异报告: {DIFF_XLSX}")
    print(f"  商品覆盖   {ws1.max_row - 1} 行")
    print(f"  BOM内容变动 {ws2.max_row - 1} 行")
    print(f"  价格变动   {ws3.max_row - 1} 行")


def main():
    bom, price = load_new()
    write_csvs(bom, price)
    build_diff(bom, price)


if __name__ == "__main__":
    main()
