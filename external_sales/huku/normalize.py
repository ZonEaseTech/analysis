"""弧酷数据 normalize 规则 — 店名 + 菜品名."""
from __future__ import annotations

import re
from typing import List, Optional


# 第三方→BQ 店编手工 override (清洗规则匹不到的)
MANUAL_STORE = {
    "NO.30 Ban Bua Thong": "030",
}


def clean_ext_store_name(s: str) -> str:
    """弧酷店名 → 清洗后小写 (用于跟 BQ 店名比对)."""
    return re.sub(r"\s+", " ",
                  re.sub(r"^NO\.?\s*\d+[.,]?\s*", "", s).strip()).lower()


def clean_bq_store_name(s: str) -> str:
    """BQ 店名 → 去泰文/中文后小写."""
    return re.sub(r"\s+", " ",
                  re.sub(r"[฀-๿一-鿿]+", "", s).strip()).lower()


def normalize_item_name(name: str) -> List[str]:
    """弧酷菜品名 → 候选名列表 (按尝试顺序). 第一个 strict 命中即取.

    规则:
      1. 原名
      2. 剥 (TH) / (TD) 后缀 (含全/半角括号)
      3. 剥 ^21 前缀 (弧酷版本号)
      4. 剥末尾数字 (Cheese ball59 → Cheese ball)
      5. 综合 (2+3+4)
    """
    n = name.strip()
    cands = [n]
    n2 = re.sub(r"\s*[\(（][TtDd]\s*[HhDd]?\s*[\)）]\s*$", "", n).strip()
    if n2 and n2 != n: cands.append(n2)
    n3 = re.sub(r"^21(?=[一-鿿])", "", n).strip()
    if n3 and n3 != n: cands.append(n3)
    n4 = re.sub(r"\d+\s*$", "", n).strip()
    if n4 and n4 != n: cands.append(n4)
    n5 = n
    n5 = re.sub(r"\s*[\(（][TtDd]\s*[HhDd]?\s*[\)）]\s*$", "", n5)
    n5 = re.sub(r"^21(?=[一-鿿])", "", n5)
    n5 = re.sub(r"\d+\s*$", "", n5).strip()
    if n5 and n5 not in cands: cands.append(n5)
    return cands


def parse_combo_part(part: str):
    """解析 '+组合' 单个成分: '2鸡腿' / '8个鸡翅根' / '半只脆皮鸡' → (qty, name)."""
    p = part.strip()
    m = re.match(r"^半[只个份]?\s*(.+)$", p)
    if m: return 0.5, m.group(1).strip()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*[个杯份只件块片]?\s*(.+)$", p)
    if m: return float(m.group(1)), m.group(2).strip()
    return 1.0, p


def strip_th_suffix(name: str) -> str:
    """剥 (TH) / (TD) 后缀 (用于 + 拆解 base name)."""
    return re.sub(r"\s*[\(（][TtDd]\s*[HhDd]?\s*[\)）]\s*$", "", name).strip()
