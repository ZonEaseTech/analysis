"""跨账本互证的行构建器 — 统计账聚合 × 凭证账聚合 → 互证行。

恒等式本体在 identities.py (CROSS_LEDGER_IDENTITIES, 口径只在那里收口);
本模块只做数据准备: 按 (store_num, item_uuid) 合并两边聚合结果.

凭证账缺行时 voucher_present=0.0 显式标记, 不伪造 0 值 — "未经互证"和
"数字为 0"是两回事 (spec §8 错误处理).
"""
from __future__ import annotations


def build_cross_ledger_rows(stat_rows: list[dict], voucher_rows: list[dict]) -> list[dict]:
    """stat_rows: 统计账聚合 (须有 store_num/item_uuid/qty/gross_amount).
    voucher_rows: 凭证账聚合 (须有 store_num/item_uuid/voucher_qty/voucher_gross).
    返回互证行, 一行一个 (store, item).
    """
    voucher_by_key = {(v["store_num"], v["item_uuid"]): v for v in voucher_rows}
    rows = []
    for s in stat_rows:
        v = voucher_by_key.get((s["store_num"], s["item_uuid"]))
        rows.append({
            "store_num": s["store_num"],
            "item_uuid": s["item_uuid"],
            "item_name": s.get("item_name", ""),
            "stat_qty": float(s.get("qty", 0) or 0),
            "stat_gross": float(s.get("gross_amount", 0) or 0),
            "voucher_qty": float(v["voucher_qty"]) if v else 0.0,
            "voucher_gross": float(v["voucher_gross"]) if v else 0.0,
            "voucher_present": 1.0 if v else 0.0,
        })
    return rows
