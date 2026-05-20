"""弧酷 → BOM 高置信度别名.

来源 (按加载顺序合并, 后到的覆盖前面):
  1. 内置默认 (本文件 BUILTIN)
  2. resources/wallace.20260514/huku_补充alias_202601.csv (市场提供的 26 条 alias)

加载: 模块 import 时一次性读 csv → SPELLING_ALIASES.
"""
from __future__ import annotations

import csv
import os


_ALIAS_CSV = "resources/wallace.20260514/huku_补充alias_202601.csv"

# 内置 alias — 优先级高于 csv (csv 由市场维护, 偶有错误时这里覆盖)
BUILTIN = {
    "脆皮手枪鸡腿饭": "脆皮手枪腿饭",   # 弧酷多一个"鸡"字
    # 修 Sheet2 alias 错误: 市场写"香草冰淇淋" 但 BOM 实际是"香草冰淇凌"
    "香草冰激凌": "香草冰淇凌",

    # 2026-05-19 用户拍板的"近似 alias" — BOM 无完全对应商品时用最近似的
    "辣番茄薯条": "辣番茄炸薯条",          # BOM 差"炸"字
    "辣番茄鸡米花": "香辣番茄鸡米花",       # BOM 无"辣番茄鸡米花", 用同辣口"香辣番茄"
    "鸡腿": "香酥鸡腿",                  # 用商品级近似 (Sheet2 BOM 有), 不用 FR01003 物料级
    "香辣鸡腿堡套餐": "香辣鸡腿堡套餐MCK",   # BOM 仅"MCK"版本
    "饮料": "可乐",                      # X+饮料 拆解时 "饮料" 按标准可乐算
    "鸡条": "鸡柳",                      # 用户确认: 鸡条就是鸡柳
    "鸡翅桶套餐": "鸡翅桶",               # 鸡翅桶 BOM 已固化 (MANUAL_BOM), 套餐按桶算
}


def _load_csv_aliases(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            k = (r.get("弧酷名") or "").strip()
            v = (r.get("BOM目标名") or "").strip()
            if k and v:
                out[k] = v
    return out


SPELLING_ALIASES = {**_load_csv_aliases(_ALIAS_CSV), **BUILTIN}
