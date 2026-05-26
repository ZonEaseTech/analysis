#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
区间销售报表 — 按起止日期任意区间出 4 个 sheet 的客户运营对账报表。

为什么独立于 report_item_sales_weekly_bq / report_daily_sales_bq:
  - 前者是"单店明细" (套餐+单品拆分), 不分平台
  - 这个是"渠道明细" (堂食 vs 外卖按 platform 横向展开), 用于对账 + 环比
  - 直接复用 semantic.sale_event_cte, 不重写 SQL

4 个 sheet:
  1. 区间汇总-长表: 商品 × 门店 × 渠道 × 子渠道, 13 个指标全展示 (对账主表)
  2. 单店汇总:    门店 × 渠道 × 子渠道, 横向透视核心指标
  3. 每日明细:    日期 × 商品 × 门店 × 渠道 × 子渠道, 13 个指标 (环比基础)
  4. 环比分析:    商品 × 门店 × 渠道 × 子渠道: 当期 vs 前推等长区间 + 量差/价差

Usage:
    venv/bin/python -m bq_reports.report_sales_period_bq \\
        --start 2026-05-19 --end 2026-05-25
    # 不传日期默认最近 7 天 (today-7 ~ yesterday)
    # 可选: --stores 001,002,003 过滤门店; --workers N 并发数

校验器: 销量恒等式 + 金额恒等式 (DEFAULT_IDENTITIES), 任一 🔴 不交付.
输出: exports/区间销售对账_<start>-<end>_vN.xlsx + MD5 指纹.
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.cloud import bigquery
from google.oauth2.credentials import Credentials
import xlsxwriter

from semantic.entities.sale_event import sale_event_cte
from semantic.validators import check, print_result
from semantic.validators.identities import DEFAULT_IDENTITIES
from semantic.reconciliation.checks.ttpos_anchor import TTPOS_NET_SALES_SQL

PROJECT_ID = "diyl-407103"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "exports"

# 从 report_item_sales_weekly_bq.py 同步, 56+4 家门店 (2026-05-25 新增 4 家)
STORE_LIST = [
    ("1", "1958987436032000"), ("2", "2269470793728000"), ("3", "2598648160256000"),
    ("4", "2876210421760000"), ("5", "3446618988544000"), ("6", "3870122057728000"),
    ("7", "4149605310464000"), ("8", "4358842359808000"), ("9", "4912616316928000"),
    ("10", "5567347171328000"), ("11", "5999171739648000"), ("12", "6542950670336000"),
    ("13", "6789240201216000"), ("14", "6977459593216000"), ("15", "7191251656704000"),
    ("16", "7400123801600000"), ("17", "7648065888256000"), ("18", "7863653113856000"),
    ("19", "8100551598080000"), ("20", "8501761941504000"), ("21", "8722592047104000"),
    ("22", "2947521978368000"), ("23", "3448951017472000"), ("24", "3782477877248000"),
    ("25", "4024875094016000"), ("26", "4229506797568000"), ("27", "4418766376960000"),
    ("28", "4613872816128000"), ("29", "4805464432640000"), ("30", "5001267122176000"),
    ("31", "5250979205120000"), ("32", "5444022046720000"), ("33", "7600687026176000"),
    ("34", "7813128523776000"), ("35", "8051063001088000"), ("36", "8535580610560000"),
    ("37", "8723170856960000"), ("38", "1515821506560000"), ("39", "3631470387200000"),
    ("40", "5498438983680000"), ("42", "9231705128960000"), ("44", "1379607252992000"),
    ("45", "1559157018624000"), ("46", "1745354756096000"), ("47", "1919875551232000"),
    ("48", "2101535051776000"), ("49", "2277263806464000"),
    ("51", "2618629820416000"), ("52", "2788834676736000"), ("53", "2992442970112000"),
    ("54", "3169446793216000"), ("55", "3367514411008000"), ("56", "3662462062592000"),
    ("58", "4053593493504000"), ("59", "4197894328320000"), ("61", "3087884357632000"),
    ("43", "1167111229440000"), ("63", "2499830353920000"),
    ("67", "5752387276800000"), ("72", "3469788319744000"),
]

# 13 个指标 + 中文 label (用户 Q3: 全展示用来对账)
METRICS = [
    "qty", "sales_price", "original_amount", "actual_amount",
    "refund_qty", "refund_amount", "free_qty", "give_qty",
    "free_amount", "give_amount", "discount_amount",
    "cancelled_qty", "cancelled_amount",
]
METRIC_LABELS = {
    "qty": "销量", "sales_price": "营业额", "original_amount": "标准金额",
    "actual_amount": "实收金额", "refund_qty": "退单数", "refund_amount": "退单金额",
    "free_qty": "免单数", "give_qty": "赠送数", "free_amount": "免单金额",
    "give_amount": "赠送金额", "discount_amount": "折扣金额",
    "cancelled_qty": "取消数", "cancelled_amount": "取消金额",
}

# 渠道展示顺序 (堂食优先, 外卖按平台字母序, pos_takeout 兜底最后)
SUBCH_PRIORITY = {"pos": 0, "pos_takeout": 99}


def log(msg: str):
    print(msg, flush=True)


def setup_proxy():
    proxy_url = os.environ.get("BQ_PROXY")
    if not proxy_url:
        return
    for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
        os.environ[k] = proxy_url


def get_creds():
    r = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, check=True,
    )
    return Credentials(
        token=r.stdout.strip(),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def parse_args():
    p = argparse.ArgumentParser(description="区间销售对账报表")
    p.add_argument("--start", help="起始日期 YYYY-MM-DD (含)")
    p.add_argument("--end", help="结束日期 YYYY-MM-DD (含)")
    p.add_argument("--stores", help="逗号分隔的门店编号过滤, 如 001,002,003")
    p.add_argument("--workers", type=int, default=8, help="并发查询门店数")
    p.add_argument("--version", type=int, help="指定版本号；不传则自动递增")
    a = p.parse_args()
    if a.start and a.end:
        sd = datetime.strptime(a.start, "%Y-%m-%d").date()
        ed = datetime.strptime(a.end, "%Y-%m-%d").date()
    else:
        ed = date.today() - timedelta(days=1)
        sd = ed - timedelta(days=6)
    if sd > ed:
        sys.exit("start 不能晚于 end")
    return sd, ed, a.stores, a.workers, a.version


def to_ts(d: date, end_of_day: bool = False) -> int:
    if end_of_day:
        return int(datetime(d.year, d.month, d.day, 23, 59, 59).timestamp())
    return int(datetime(d.year, d.month, d.day, 0, 0, 0).timestamp())


def next_version(out_dir: Path, prefix: str) -> int:
    pat = re.compile(rf"^{re.escape(prefix)}_v(\d+)\.xlsx$")
    used = []
    if out_dir.exists():
        for p in out_dir.iterdir():
            m = pat.match(p.name)
            if m:
                used.append(int(m.group(1)))
    return (max(used) + 1) if used else 1


def build_sql(store_num: str, dataset_id: str, start_ts: int, end_ts: int) -> str:
    """拼一店的 query: sale_event(含 business_date) + 商品名 JOIN."""
    cte = sale_event_cte(with_business_date=True)
    select_metrics = ",\n      ".join(f"SUM(se.{m}) AS {m}" for m in METRICS)
    template = (
        "WITH " + cte + f""",
items AS (
  SELECT
    '{store_num}' AS store_num,
    se.item_uuid,
    COALESCE(NULLIF(JSON_EXTRACT_SCALAR(pp.name, '$.zh'), 'null'),
             NULLIF(JSON_EXTRACT_SCALAR(pp.name, '$.en'), 'null'),
             CAST(se.item_uuid AS STRING)) AS item_name,
    se.business_date,
    se.channel,
    se.sub_channel,
      {select_metrics}
  FROM sale_event se
  LEFT JOIN `{{project}}`.`{{dataset}}`.`ttpos_product_package` pp
    ON pp.uuid = se.item_uuid
  GROUP BY store_num, item_uuid, item_name, business_date, channel, sub_channel
)
SELECT * FROM items
"""
    )
    return template.format(
        project=PROJECT_ID,
        dataset=dataset_id,
        start_ts=start_ts,
        end_ts=end_ts,
    )


def query_one_store(client, store_num, store_uuid, start_ts, end_ts):
    dataset_id = f"shop{store_uuid}"
    sql = build_sql(store_num, dataset_id, start_ts, end_ts)
    rows = []
    try:
        for r in client.query(sql).result():
            rows.append({
                "store_num": store_num,
                "item_uuid": r.item_uuid,
                "item_name": r.item_name,
                "business_date": r.business_date.isoformat() if r.business_date else "",
                "channel": r.channel,
                "sub_channel": r.sub_channel,
                **{m: float(getattr(r, m) or 0) for m in METRICS},
            })
    except Exception as e:
        log(f"[{store_num}] query FAILED: {e}")
    return rows


def query_ttpos_anchor(start_ts, end_ts, store_list, workers=8) -> dict:
    """跑 ttpos 后台首页营业总览等价 SQL (CountProductSale + CountTakeoutSale.platform_total).
    返回 {store_num: 营业总览金额} — 用于跟我们 sale_event 的 actual_amount 对账.

    存在意义: 防止未来 ttpos 启用税/服务费/商家折扣后, 我们数字偏离首页营业总览
    而客户没发现 → 信任崩盘. 这一层是"对账自动监控", 不是数据计算.
    """
    setup_proxy()
    client = bigquery.Client(project=PROJECT_ID, credentials=get_creds())
    result = {}
    def _query(sn, su):
        ds = f"shop{su}"
        sql = TTPOS_NET_SALES_SQL.format(project=PROJECT_ID, dataset=ds,
                                          start_ts=start_ts, end_ts=end_ts)
        try:
            r = list(client.query(sql).result())[0]
            return sn, float(r.ttpos_net_sales or 0)
        except Exception as e:
            log(f"  [anchor {sn}] FAILED: {e}")
            return sn, None
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_query, sn, su) for sn, su in store_list]
        for fut in as_completed(futures):
            sn, v = fut.result()
            if v is not None:
                result[sn] = v
    return result


def write_sheet_anchor(wb, anchor_by_store: dict, rows: list):
    """Sheet「ttpos 对账锚」: 逐店列 ttpos 营业总览口径 vs 我们 actual_amount.
    任一店差异 > 0.1% 标红, 0.01-0.1% 标黄, < 0.01% 绿.
    """
    ws = wb.add_worksheet("ttpos对账锚")
    hdr = _header_fmt(wb); money = _money_fmt(wb); title = _title_fmt(wb)
    pct_fmt = wb.add_format({"num_format": "0.0000%"})
    green = wb.add_format({"bg_color": "#C6EFCE"})
    yellow = wb.add_format({"bg_color": "#FFEB9C"})
    red = wb.add_format({"bg_color": "#FFC7CE", "bold": True})

    ws.merge_range(0, 0, 0, 5, "ttpos 后台营业总览口径 vs 本报表实收对账", title)
    ws.write(1, 0, "意义: ttpos 后台首页用 CountTakeoutSale (platform_total) 算外卖; "
                    "本报表用 RankTakeoutProduct (price×qty). 华莱士现状两者一致 = 0 差异; "
                    "未来启用税/服务费/商家折扣后会偏离, 本表自动监控.")
    cols = ["门店编号", "ttpos营业总览口径", "本报表实收", "差异", "差异率", "状态"]
    for i, c in enumerate(cols):
        ws.write(3, i, c, hdr)

    ours_by_store = {}
    for r in rows:
        ours_by_store[r["store_num"]] = ours_by_store.get(r["store_num"], 0) + (r.get("actual_amount") or 0)

    ri = 4
    tot_a = 0.0; tot_o = 0.0
    breaches = []
    for sn in sorted(set(anchor_by_store.keys()) | set(ours_by_store.keys()),
                     key=lambda s: int(s) if s.isdigit() else 999):
        a = anchor_by_store.get(sn, 0.0)
        o = ours_by_store.get(sn, 0.0)
        diff = a - o
        rate = (diff / a) if a else 0.0
        if abs(rate) < 0.0001:   # < 0.01%
            status, fmt = "✅ 一致", green
        elif abs(rate) < 0.001:  # < 0.1%
            status, fmt = "⚠️ 需复核", yellow
            breaches.append((sn, diff, rate))
        else:
            status, fmt = "🔴 必查", red
            breaches.append((sn, diff, rate))
        ws.write(ri, 0, sn, fmt)
        ws.write(ri, 1, a, money); ws.write(ri, 2, o, money)
        ws.write(ri, 3, diff, money); ws.write(ri, 4, rate, pct_fmt)
        ws.write(ri, 5, status, fmt)
        tot_a += a; tot_o += o
        ri += 1
    # 总计
    diff_t = tot_a - tot_o
    rate_t = (diff_t / tot_a) if tot_a else 0.0
    ws.write(ri, 0, "TOTAL", _title_fmt(wb))
    ws.write(ri, 1, tot_a, money); ws.write(ri, 2, tot_o, money)
    ws.write(ri, 3, diff_t, money); ws.write(ri, 4, rate_t, pct_fmt)
    ws.write(ri, 5, "✅ 全店一致" if not breaches else f"⚠️ {len(breaches)} 店偏离", _title_fmt(wb))
    ws.set_column(0, 0, 10); ws.set_column(1, 4, 18); ws.set_column(5, 5, 12)
    return breaches, diff_t, rate_t


def query_all(start_ts, end_ts, store_list, workers=8) -> list:
    setup_proxy()
    client = bigquery.Client(project=PROJECT_ID, credentials=get_creds())
    all_rows = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(query_one_store, client, sn, su, start_ts, end_ts): sn
            for sn, su in store_list
        }
        done = 0
        for fut in as_completed(futures):
            sn = futures[fut]
            rows = fut.result()
            all_rows.extend(rows)
            done += 1
            if done % 10 == 0 or done == len(store_list):
                log(f"  进度 {done}/{len(store_list)} 店 — 累计 {len(all_rows)} 行")
    log(f"  query 总耗时 {time.time()-t0:.1f}s")
    return all_rows


# ---------- 聚合 / 透视 ----------

def aggregate(rows, group_keys, item_name_key="item_name"):
    """通用聚合: 按 group_keys 把 METRICS 加起来. 返回 list[dict]."""
    bucket = {}
    for r in rows:
        key = tuple(r.get(k) for k in group_keys)
        agg = bucket.setdefault(key, {k: r.get(k) for k in group_keys})
        # item_name 保留首次出现的
        if item_name_key in r and item_name_key not in agg:
            agg[item_name_key] = r[item_name_key]
        for m in METRICS:
            agg[m] = agg.get(m, 0.0) + (r.get(m) or 0.0)
    return list(bucket.values())


def collect_subchannels(rows) -> list:
    """返回按 SUBCH_PRIORITY 排序后的 sub_channel 列表."""
    seen = set(r["sub_channel"] for r in rows)
    return sorted(seen, key=lambda s: (SUBCH_PRIORITY.get(s, 50), s))


# ---------- Sheet 写出 ----------

def _money_fmt(wb):
    return wb.add_format({"num_format": "#,##0.00"})


def _int_fmt(wb):
    return wb.add_format({"num_format": "#,##0"})


def _header_fmt(wb):
    return wb.add_format({"bold": True, "bg_color": "#D9E1F2", "border": 1, "align": "center"})


def _title_fmt(wb):
    return wb.add_format({"bold": True, "font_size": 12, "bg_color": "#FFF2CC"})


def write_sheet1_period_long(wb, rows):
    """Sheet 1: 区间汇总-长表, 行=商品×门店×渠道×子渠道."""
    ws = wb.add_worksheet("区间汇总-长表")
    hdr = _header_fmt(wb); money = _money_fmt(wb); int_f = _int_fmt(wb)
    cols = ["门店编号", "商品名", "item_uuid", "渠道", "子渠道"] + [METRIC_LABELS[m] for m in METRICS]
    for i, c in enumerate(cols):
        ws.write(0, i, c, hdr)
    agg = aggregate(rows, ["store_num", "item_uuid", "channel", "sub_channel"])
    agg.sort(key=lambda r: (r["store_num"], r["item_name"] or "", r["channel"], r["sub_channel"]))
    for ri, r in enumerate(agg, start=1):
        ws.write(ri, 0, r["store_num"])
        ws.write(ri, 1, r.get("item_name", "") or "")
        ws.write(ri, 2, str(r["item_uuid"]))
        ws.write(ri, 3, r["channel"])
        ws.write(ri, 4, r["sub_channel"])
        for ci, m in enumerate(METRICS, start=5):
            fmt = int_f if m.endswith("_qty") else money
            ws.write(ri, ci, r.get(m, 0), fmt)
    ws.freeze_panes(1, 5)
    ws.set_column(0, 0, 8); ws.set_column(1, 1, 30); ws.set_column(2, 2, 18)
    ws.set_column(3, 4, 10); ws.set_column(5, 4 + len(METRICS), 14)
    return len(agg)


def write_sheet2_store_pivot(wb, rows):
    """Sheet 2: 单店汇总, 行=门店, 列=子渠道横向展开核心指标."""
    ws = wb.add_worksheet("单店汇总")
    hdr = _header_fmt(wb); money = _money_fmt(wb); int_f = _int_fmt(wb)
    subchs = collect_subchannels(rows)
    # 核心 3 指标横向展开: 销量/营业额/实收
    core_metrics = ["qty", "sales_price", "actual_amount"]
    cols = ["门店编号"]
    for sc in subchs:
        for m in core_metrics:
            cols.append(f"{sc}·{METRIC_LABELS[m]}")
    cols += ["总" + METRIC_LABELS[m] for m in core_metrics]
    for i, c in enumerate(cols):
        ws.write(0, i, c, hdr)
    agg = aggregate(rows, ["store_num", "sub_channel"])
    by_store = {}
    for r in agg:
        by_store.setdefault(r["store_num"], {})[r["sub_channel"]] = r
    for ri, store_num in enumerate(sorted(by_store.keys()), start=1):
        ws.write(ri, 0, store_num)
        ci = 1
        totals = {m: 0.0 for m in core_metrics}
        for sc in subchs:
            r = by_store[store_num].get(sc, {})
            for m in core_metrics:
                v = r.get(m, 0) or 0
                totals[m] += v
                fmt = int_f if m.endswith("_qty") else money
                ws.write(ri, ci, v, fmt); ci += 1
        for m in core_metrics:
            fmt = int_f if m.endswith("_qty") else money
            ws.write(ri, ci, totals[m], fmt); ci += 1
    ws.freeze_panes(1, 1)
    ws.set_column(0, 0, 10); ws.set_column(1, len(cols)-1, 14)
    return len(by_store)


def write_sheet3_daily(wb, rows):
    """Sheet 3: 每日明细, 行=日期×商品×门店×渠道×子渠道."""
    ws = wb.add_worksheet("每日明细")
    hdr = _header_fmt(wb); money = _money_fmt(wb); int_f = _int_fmt(wb)
    cols = ["日期", "门店编号", "商品名", "item_uuid", "渠道", "子渠道"] + [METRIC_LABELS[m] for m in METRICS]
    for i, c in enumerate(cols):
        ws.write(0, i, c, hdr)
    agg = aggregate(rows, ["business_date", "store_num", "item_uuid", "channel", "sub_channel"])
    agg.sort(key=lambda r: (r["business_date"], r["store_num"], r["item_name"] or "", r["channel"], r["sub_channel"]))
    for ri, r in enumerate(agg, start=1):
        ws.write(ri, 0, r["business_date"])
        ws.write(ri, 1, r["store_num"])
        ws.write(ri, 2, r.get("item_name", "") or "")
        ws.write(ri, 3, str(r["item_uuid"]))
        ws.write(ri, 4, r["channel"])
        ws.write(ri, 5, r["sub_channel"])
        for ci, m in enumerate(METRICS, start=6):
            fmt = int_f if m.endswith("_qty") else money
            ws.write(ri, ci, r.get(m, 0), fmt)
    ws.freeze_panes(1, 6)
    ws.set_column(0, 0, 12); ws.set_column(1, 1, 8); ws.set_column(2, 2, 30)
    ws.set_column(3, 3, 18); ws.set_column(4, 5, 10); ws.set_column(6, 5+len(METRICS), 14)
    return len(agg)


def write_sheet4_variance(wb, curr_rows, prev_rows, curr_period, prev_period):
    """Sheet 4: 环比分析 — 当期 vs 前推等长区间.

    grain = 商品×门店×渠道×子渠道. 计算 当期/上期 + 总差异 + 量差 + 价差.
    """
    ws = wb.add_worksheet("环比分析")
    hdr = _header_fmt(wb); money = _money_fmt(wb); int_f = _int_fmt(wb); title = _title_fmt(wb)
    ws.merge_range(0, 0, 0, 8, f"环比: 当期 {curr_period} vs 上期 {prev_period}", title)
    cols = [
        "门店编号", "商品名", "渠道", "子渠道",
        "当期销量", "上期销量", "当期营业额", "上期营业额",
        "总差异", "量差", "价差", "差异占比",
    ]
    for i, c in enumerate(cols):
        ws.write(1, i, c, hdr)

    def keymap(rs):
        agg = aggregate(rs, ["store_num", "item_uuid", "channel", "sub_channel"])
        m = {}
        for r in agg:
            k = (r["store_num"], r["item_uuid"], r["channel"], r["sub_channel"])
            m[k] = r
        return m

    curr_map = keymap(curr_rows)
    prev_map = keymap(prev_rows)
    all_keys = sorted(set(curr_map.keys()) | set(prev_map.keys()))

    ri = 2
    for k in all_keys:
        cur = curr_map.get(k, {})
        prv = prev_map.get(k, {})
        item_name = cur.get("item_name") or prv.get("item_name") or ""
        cq = cur.get("qty", 0) or 0
        pq = prv.get("qty", 0) or 0
        cr = cur.get("sales_price", 0) or 0
        pr = prv.get("sales_price", 0) or 0
        total_diff = cr - pr
        # 量差 = ΔQ × 上期均价；价差 = ΔP × 当期销量 (剩余)
        prev_avg_price = (pr / pq) if pq else 0
        volume_var = (cq - pq) * prev_avg_price
        price_var = total_diff - volume_var
        pct = (total_diff / pr) if pr else None
        ws.write(ri, 0, k[0])
        ws.write(ri, 1, item_name)
        ws.write(ri, 2, k[2])
        ws.write(ri, 3, k[3])
        ws.write(ri, 4, cq, int_f); ws.write(ri, 5, pq, int_f)
        ws.write(ri, 6, cr, money); ws.write(ri, 7, pr, money)
        ws.write(ri, 8, total_diff, money)
        ws.write(ri, 9, volume_var, money)
        ws.write(ri, 10, price_var, money)
        if pct is not None:
            ws.write(ri, 11, pct, wb.add_format({"num_format": "0.00%"}))
        ri += 1
    ws.freeze_panes(2, 4)
    ws.set_column(0, 0, 8); ws.set_column(1, 1, 30); ws.set_column(2, 3, 10)
    ws.set_column(4, 11, 14)
    return ri - 2


def write_meta_sheet(wb, version: int, start, end, prev_start, prev_end, totals: dict):
    ws = wb.add_worksheet("说明")
    bold_red = wb.add_format({"bold": True, "font_color": "red", "font_size": 14})
    ws.write(0, 0, f"区间销售对账报表 — 内部版本 v{version}", bold_red)
    ws.write(2, 0, "当期区间"); ws.write(2, 1, f"{start} ~ {end}")
    ws.write(3, 0, "上期区间"); ws.write(3, 1, f"{prev_start} ~ {prev_end}")
    ws.write(4, 0, "生成时间"); ws.write(4, 1, datetime.now().isoformat(timespec="seconds"))
    ws.write(6, 0, "Sheet 行数统计:")
    r = 7
    for k, v in totals.items():
        ws.write(r, 0, k); ws.write(r, 1, v)
        r += 1
    ws.write(r + 1, 0, "口径说明:")
    notes = [
        "1. sub_channel: 'pos'=堂食 POS, 'pos_takeout'=外卖 POS 本地下单, 其余=外卖平台(grab/lineman/...)",
        "2. 营业额=标价×销量; 实收金额=ttpos CountProductSale 真实口径 (扣赠送/退款/用成交价)",
        "3. 外卖无赠送/折扣概念, 对应字段固定 0; 取消单独算 cancelled_*",
        "4. 环比: 当期 vs 前推等长天数. 量差=ΔQ×上期均价, 价差=总差异-量差",
        "5. 校验器跑销量+金额恒等式 (DEFAULT_IDENTITIES), 详见 console 日志",
    ]
    for n in notes:
        r += 1; ws.write(r, 0, n)
    ws.set_column(0, 0, 16); ws.set_column(1, 1, 60)


def run_validators(rows, label: str):
    """跑销量+金额恒等式. 按 (store_num, item_uuid) 聚合再 check."""
    log(f"\n=== 校验器: {label} ===")
    # validator 要 net_qty / revenue 字段 — 用 actual_amount 当 revenue, qty - others 当 net_qty
    check_rows = []
    for r in aggregate(rows, ["store_num", "item_uuid", "channel"]):
        cq = r.get("cancelled_qty", 0) or 0
        rq = r.get("refund_qty", 0) or 0
        fq = r.get("free_qty", 0) or 0
        gq = r.get("give_qty", 0) or 0
        check_rows.append({
            "store_num": r["store_num"],
            "item_uuid": r["item_uuid"],
            "qty": r.get("qty", 0),
            "net_qty": r.get("qty", 0) - rq - fq - gq - cq,
            "free_qty": fq, "give_qty": gq, "refund_qty": rq, "cancelled_qty": cq,
            "sales_price": r.get("sales_price", 0),
            "revenue": r.get("actual_amount", 0),
            "refund_amount": r.get("refund_amount", 0),
            "free_amount": r.get("free_amount", 0),
            "give_amount": r.get("give_amount", 0),
            "discount_amount": r.get("discount_amount", 0),
            "cancelled_amount": r.get("cancelled_amount", 0),
        })
    result = check(check_rows, DEFAULT_IDENTITIES)
    print_result(result, row_label=lambda r: f"店 {r['store_num']} item {r['item_uuid']}")
    if result.has_must_fix:
        log("⚠️  有 🔴 离谱违反, 但继续出表 (运营对账场景, 让用户自己看差异源).")
    return result


def md5_file(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    sd, ed, stores_filter, workers, ver_override = parse_args()
    # 当期 & 上期
    span_days = (ed - sd).days + 1
    prev_ed = sd - timedelta(days=1)
    prev_sd = prev_ed - timedelta(days=span_days - 1)
    log(f"当期: {sd} ~ {ed} ({span_days} 天)")
    log(f"上期: {prev_sd} ~ {prev_ed}")

    store_list = STORE_LIST
    if stores_filter:
        wanted = set(s.strip().lstrip("0") for s in stores_filter.split(",") if s.strip())
        store_list = [(sn, su) for sn, su in store_list if sn.lstrip("0") in wanted]
        log(f"门店过滤: {len(store_list)} 家")

    log("\n[1/2] 查当期数据...")
    curr_rows = query_all(to_ts(sd), to_ts(ed, end_of_day=True), store_list, workers)
    log(f"\n[2/2] 查上期数据 (用于环比)...")
    prev_rows = query_all(to_ts(prev_sd), to_ts(prev_ed, end_of_day=True), store_list, workers)

    if not curr_rows:
        sys.exit("⚠️  当期无数据, 终止.")

    # 校验
    run_validators(curr_rows, f"当期 {sd}~{ed}")

    # 输出
    prefix = f"区间销售对账_{sd.strftime('%Y%m%d')}-{ed.strftime('%Y%m%d')}"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    version = ver_override or next_version(OUTPUT_DIR, prefix)
    out_path = OUTPUT_DIR / f"{prefix}_v{version}.xlsx"
    log(f"\n写出 → {out_path}")

    wb = xlsxwriter.Workbook(out_path)
    totals = {}
    totals["区间汇总-长表"] = write_sheet1_period_long(wb, curr_rows)
    totals["单店汇总"] = write_sheet2_store_pivot(wb, curr_rows)
    totals["每日明细"] = write_sheet3_daily(wb, curr_rows)
    totals["环比分析"] = write_sheet4_variance(
        wb, curr_rows, prev_rows,
        curr_period=f"{sd}~{ed}", prev_period=f"{prev_sd}~{prev_ed}",
    )

    # ttpos 营业总览口径对账锚 — 防止未来口径偏离不被发现
    log("\n[anchor] 跑 ttpos 营业总览口径对账...")
    anchor_by_store = query_ttpos_anchor(to_ts(sd), to_ts(ed, end_of_day=True), store_list, workers)
    breaches, diff_t, rate_t = write_sheet_anchor(wb, anchor_by_store, curr_rows)
    totals["ttpos对账锚"] = len(anchor_by_store)
    if breaches:
        log(f"  ⚠️  {len(breaches)} 店偏离 ttpos 营业总览口径:")
        for sn, d, r in breaches[:10]:
            log(f"     店{sn} 差异 {d:>10,.2f}  rate={r*100:.4f}%")
        log("  排查方向: 是否启用了税/商家服务费/商家折扣? 见 docs/ttpos-algorithms-mirror.md §2.1")
    else:
        log(f"  ✅ 全 {len(anchor_by_store)} 店跟 ttpos 营业总览 byte-equal 一致 (差额 {diff_t:,.2f}, {rate_t*100:.4f}%)")

    write_meta_sheet(wb, version, sd, ed, prev_sd, prev_ed, totals)
    wb.close()

    md5 = md5_file(out_path)
    size = out_path.stat().st_size
    log(f"\n输出: {out_path}")
    log(f"  内部版本: v{version}")
    log(f"  修改时间: {datetime.fromtimestamp(out_path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"  大小:     {size} bytes")
    log(f"  MD5:      {md5}")
    log(f"  Sheet 行数: {totals}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
