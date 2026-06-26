#!/usr/bin/env python3
# 谁问的: 何伟涛 / 2026-06-15
# 问什么: 利润表BOM明细那19个"无BOM套餐"(紫薯脆波波后已剩促销/抹茶等), 同事《成本毛利分析》里其实有配方;
#         把同事文件的套餐配方抽成标准 BOM CSV, 接成 config 低优先级兜底层, 补齐无BOM套餐。
# 结论:   已沉淀 — 清洗产物 同事套餐BOM_202605.csv, 接 config bom_sources(低 priority, 仅补订单回溯/最终BOM 未覆盖的套餐)。
"""清洗同事《Wallace商品成本毛利分析_2026-05》→ 标准套餐 BOM CSV。

源 (类型B, 多sheet, 商品名跨行承接): 堂食-套餐 / 外卖-套餐 两 sheet。
  列: 门店/毛利率/.../商品/单价/BOM物品名称/BOM物品编码/消耗数量/物品单价/单位/...
  商品(套餐名)只在每个 block 首行有值, 其余行 None → 需 forward-fill。
  消耗数量 = 单份配方 (跟 最终BOM_202605.csv 同口径)。

产物 (跟 最终BOM_202605.csv 同格式, 可直接走 CSVAdapter bom_sources):
  商品名称, 物品编号, 物品名称, 单耗, 单位
去重: (套餐名, 物品编号) 取首个非空单耗; 堂食优先, 堂食没有的套餐用外卖补。
"""
import csv
import openpyxl

RAW = "resources/wallace.20260615/同事成本毛利分析_2026-05_原始.xlsx"
OUT = "resources/wallace.20260615/同事套餐BOM_202605.csv"


def extract(ws):
    """forward-fill 商品名, 收 (套餐名 -> {物品编号: (物品名, 单耗, 单位)})。"""
    it = ws.iter_rows(values_only=True)
    hdr = list(next(it))
    idx = {str(h).strip(): i for i, h in enumerate(hdr) if h is not None}
    cP, cBn, cBc = idx["商品"], idx["BOM物品名称"], idx["BOM物品编码"]
    cQ, cU = idx["消耗数量"], idx["单位"]
    cur = None
    out = {}
    for r in it:
        if r[cP] not in (None, ""):
            cur = str(r[cP]).strip()
        code = r[cBc]
        if not cur or code in (None, ""):
            continue
        name = str(cur)
        code = str(code).strip()
        if name not in out:
            out[name] = {}
        if code not in out[name]:
            out[name][code] = (
                (str(r[cBn]).strip() if r[cBn] else ""),
                float(r[cQ] or 0),
                (str(r[cU]).strip() if r[cU] else ""),
            )
    return out


def main():
    wb = openpyxl.load_workbook(RAW, data_only=True, read_only=True)
    dine = extract(wb["堂食-套餐"])
    takeout = extract(wb["外卖-套餐"])
    wb.close()
    # 合并: 堂食优先, 堂食没有的套餐用外卖
    combos = dict(takeout)        # 先放外卖
    combos.update(dine)           # 堂食覆盖 (同名)
    rows = []
    for combo in sorted(combos):
        for code, (mname, qty, unit) in combos[combo].items():
            rows.append([combo, code, mname, qty, unit])
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["商品名称", "物品编号", "物品名称", "单耗", "单位"])
        w.writerows(rows)
    print(f"[clean] 堂食套餐 {len(dine)} / 外卖套餐 {len(takeout)} / 合并 {len(combos)} 个套餐")
    print(f"[clean] 输出 {OUT}: {len(rows)} 行")
    # 抽查那 7 个缺的
    for kw in ("紫薯脆波波", "鸡肉卷，第二份", "买一份薯条", "抹茶冰淇淋", "整鸡套餐", "双主套", "脆皮鸡肉汉堡，第二份", "新学期 1"):
        hit = [c for c in combos if kw in c]
        if hit:
            c = hit[0]
            print(f"  ✓ {c[:24]:24} {len(combos[c])} 物料")


if __name__ == "__main__":
    main()
