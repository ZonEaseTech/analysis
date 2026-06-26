"""跨账本互证的行构建器 — 统计账聚合 × 凭证账聚合 → 互证行。

恒等式本体在 identities.py (CROSS_LEDGER_IDENTITIES, 口径只在那里收口);
本模块只做数据准备: 按 (store_num, item_uuid) 合并两边聚合结果.

凭证账缺行时 voucher_present=0.0 显式标记, 不伪造 0 值 — "未经互证"和
"数字为 0"是两回事 (spec §8 错误处理).

孤儿凭证行 (凭证有/统计无) 同样产出, stat_qty=0 让销量互证自动 fire.
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
    # 孤儿凭证行: 凭证账有 (store, item) 而统计账没有 = 统计侧丢写入 —
    # 互证要抓的对称失败模式, 产出 stat_qty=0 行让 CROSS_LEDGER_QTY 自动 fire.
    stat_keys = {(s["store_num"], s["item_uuid"]) for s in stat_rows}
    for (store, item), v in voucher_by_key.items():
        if (store, item) in stat_keys:
            continue
        rows.append({
            "store_num": store,
            "item_uuid": item,
            "item_name": "",
            "stat_qty": 0.0,
            "stat_gross": 0.0,
            "voucher_qty": float(v["voucher_qty"]),
            "voucher_gross": float(v["voucher_gross"]),
            "voucher_present": 1.0,
        })
    return rows
