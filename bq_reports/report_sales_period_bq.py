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

from semantic.dimensions.time import assert_month_not_frozen
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

# 渠道 / 子渠道中文翻译 — 财务/运营看不懂英文 ID, 报表展示用
CHANNEL_LABEL = {"dine": "堂食", "takeout": "外卖"}
SUBCH_LABEL = {
    "pos": "POS堂食",
    "pos_takeout": "外卖POS自接",
    "grab": "Grab", "lineman": "Lineman", "shopee": "Shopee",
    "foodpanda": "FoodPanda", "robinhood": "Robinhood",
}


def fmt_channel(ch: str) -> str:
    return CHANNEL_LABEL.get(ch, ch or "")


def fmt_subch(sc: str) -> str:
    return SUBCH_LABEL.get(sc, sc or "")


def fetch_store_names(store_list, workers=10) -> dict:
    """从 BQ ttpos_setting 拉门店名 (权威源, 跟 POS 实时一致).
    返回 {store_num: store_name}. 任一店查不到不致命, 报表那行显示空字符串.
    """
    setup_proxy()
    client = bigquery.Client(project=PROJECT_ID, credentials=get_creds())
    mapping = {}
    def _q(sn, su):
        sql = (f"SELECT JSON_VALUE(values, '$.name') AS name "
               f"FROM `{PROJECT_ID}.shop{su}.ttpos_setting` "
               f"WHERE key='store' AND delete_time=0 LIMIT 1")
        try:
            for r in client.query(sql).result():
                if r.name:
                    return sn, str(r.name).strip()
        except Exception:
            pass
        return sn, ""
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(_q, sn, su) for sn, su in store_list]):
            sn, name = f.result()
            if name:
                mapping[sn] = name
    return mapping


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
    p.add_argument("--force", action="store_true",
                   help="强制导出即使校验未通过 (文件将带水印, 不得对外交付)")
    a = p.parse_args()
    if a.start and a.end:
        sd = datetime.strptime(a.start, "%Y-%m-%d").date()
        ed = datetime.strptime(a.end, "%Y-%m-%d").date()
    else:
        ed = date.today() - timedelta(days=1)
        sd = ed - timedelta(days=6)
    if sd > ed:
        sys.exit("start 不能晚于 end")
    return sd, ed, a.stores, a.workers, a.version, a.force


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


def write_sheet1_period_long(wb, rows, store_names: dict):
    """Sheet 1: 区间汇总-长表, 行=商品×门店×渠道×子渠道, 含门店小计 + 总计."""
    ws = wb.add_worksheet("区间汇总-长表")
    hdr = _header_fmt(wb); money = _money_fmt(wb); int_f = _int_fmt(wb)
    sub = wb.add_format({"bold": True, "bg_color": "#FFF2CC", "num_format": "#,##0.00"})
    sub_i = wb.add_format({"bold": True, "bg_color": "#FFF2CC", "num_format": "#,##0"})
    gtotal = wb.add_format({"bold": True, "bg_color": "#D9E1F2", "num_format": "#,##0.00", "border": 1})
    gtotal_i = wb.add_format({"bold": True, "bg_color": "#D9E1F2", "num_format": "#,##0", "border": 1})

    cols = ["门店编号", "门店名", "商品名", "item_uuid", "渠道", "子渠道"] + [METRIC_LABELS[m] for m in METRICS]
    for i, c in enumerate(cols):
        ws.write(0, i, c, hdr)
    agg = aggregate(rows, ["store_num", "item_uuid", "channel", "sub_channel"])
    agg.sort(key=lambda r: (int(r["store_num"]) if r["store_num"].isdigit() else 999,
                              r["item_name"] or "", r["channel"], r["sub_channel"]))
    ri = 1
    grand = {m: 0.0 for m in METRICS}
    store_agg = {m: 0.0 for m in METRICS}
    cur_store = None
    def _flush_store_subtotal(target_row, sn):
        ws.write(target_row, 0, f"{sn} 小计", sub)
        ws.write(target_row, 1, store_names.get(sn, ""), sub)
        ws.write(target_row, 2, "", sub); ws.write(target_row, 3, "", sub)
        ws.write(target_row, 4, "", sub); ws.write(target_row, 5, "", sub)
        for ci, m in enumerate(METRICS, start=6):
            ws.write(target_row, ci, store_agg[m], sub_i if m.endswith("_qty") else sub)
    for r in agg:
        if cur_store is not None and r["store_num"] != cur_store:
            _flush_store_subtotal(ri, cur_store)
            ri += 1
            store_agg = {m: 0.0 for m in METRICS}
        cur_store = r["store_num"]
        ws.write(ri, 0, r["store_num"])
        ws.write(ri, 1, store_names.get(r["store_num"], ""))
        ws.write(ri, 2, r.get("item_name", "") or "")
        ws.write(ri, 3, str(r["item_uuid"]))
        ws.write(ri, 4, fmt_channel(r["channel"]))
        ws.write(ri, 5, fmt_subch(r["sub_channel"]))
        for ci, m in enumerate(METRICS, start=6):
            fmt = int_f if m.endswith("_qty") else money
            v = r.get(m, 0) or 0
            ws.write(ri, ci, v, fmt)
            grand[m] += v; store_agg[m] += v
        ri += 1
    if cur_store is not None:
        _flush_store_subtotal(ri, cur_store)
        ri += 1
    # 总计行
    ws.write(ri, 0, "总计", gtotal)
    for ci in range(1, 6): ws.write(ri, ci, "", gtotal)
    for ci, m in enumerate(METRICS, start=6):
        ws.write(ri, ci, grand[m], gtotal_i if m.endswith("_qty") else gtotal)
    ws.freeze_panes(1, 6)
    ws.set_column(0, 0, 8); ws.set_column(1, 1, 18); ws.set_column(2, 2, 30)
    ws.set_column(3, 3, 18); ws.set_column(4, 5, 11); ws.set_column(6, 5 + len(METRICS), 14)
    return len(agg)


def write_sheet2_store_pivot(wb, rows, store_names: dict):
    """Sheet 2: 单店汇总, 行=门店, 列=子渠道横向展开核心指标 + 总计行."""
    ws = wb.add_worksheet("单店汇总")
    hdr = _header_fmt(wb); money = _money_fmt(wb); int_f = _int_fmt(wb)
    gtotal = wb.add_format({"bold": True, "bg_color": "#D9E1F2", "num_format": "#,##0.00", "border": 1})
    gtotal_i = wb.add_format({"bold": True, "bg_color": "#D9E1F2", "num_format": "#,##0", "border": 1})

    subchs = collect_subchannels(rows)
    core_metrics = ["qty", "sales_price", "actual_amount"]
    cols = ["门店编号", "门店名"]
    for sc in subchs:
        for m in core_metrics:
            cols.append(f"{fmt_subch(sc)}·{METRIC_LABELS[m]}")
    cols += ["总" + METRIC_LABELS[m] for m in core_metrics]
    for i, c in enumerate(cols):
        ws.write(0, i, c, hdr)
    agg = aggregate(rows, ["store_num", "sub_channel"])
    by_store = {}
    for r in agg:
        by_store.setdefault(r["store_num"], {})[r["sub_channel"]] = r
    grand_pivot = {sc: {m: 0.0 for m in core_metrics} for sc in subchs}
    grand_total = {m: 0.0 for m in core_metrics}
    sorted_stores = sorted(by_store.keys(), key=lambda s: int(s) if s.isdigit() else 999)
    for ri, store_num in enumerate(sorted_stores, start=1):
        ws.write(ri, 0, store_num)
        ws.write(ri, 1, store_names.get(store_num, ""))
        ci = 2
        totals = {m: 0.0 for m in core_metrics}
        for sc in subchs:
            r = by_store[store_num].get(sc, {})
            for m in core_metrics:
                v = r.get(m, 0) or 0
                totals[m] += v; grand_pivot[sc][m] += v
                fmt = int_f if m.endswith("_qty") else money
                ws.write(ri, ci, v, fmt); ci += 1
        for m in core_metrics:
            fmt = int_f if m.endswith("_qty") else money
            ws.write(ri, ci, totals[m], fmt); ci += 1
            grand_total[m] += totals[m]
    # 总计行
    ri = len(sorted_stores) + 1
    ws.write(ri, 0, "总计", gtotal); ws.write(ri, 1, "", gtotal)
    ci = 2
    for sc in subchs:
        for m in core_metrics:
            ws.write(ri, ci, grand_pivot[sc][m], gtotal_i if m.endswith("_qty") else gtotal); ci += 1
    for m in core_metrics:
        ws.write(ri, ci, grand_total[m], gtotal_i if m.endswith("_qty") else gtotal); ci += 1
    ws.freeze_panes(1, 2)
    ws.set_column(0, 0, 10); ws.set_column(1, 1, 18); ws.set_column(2, len(cols)-1, 14)
    return len(by_store)


def write_sheet3_daily(wb, rows, store_names: dict):
    """Sheet 3: 每日明细, 行=日期×商品×门店×渠道×子渠道 (不加小计, 行数太多)."""
    ws = wb.add_worksheet("每日明细")
    hdr = _header_fmt(wb); money = _money_fmt(wb); int_f = _int_fmt(wb)
    cols = ["日期", "门店编号", "门店名", "商品名", "item_uuid", "渠道", "子渠道"] + [METRIC_LABELS[m] for m in METRICS]
    for i, c in enumerate(cols):
        ws.write(0, i, c, hdr)
    agg = aggregate(rows, ["business_date", "store_num", "item_uuid", "channel", "sub_channel"])
    agg.sort(key=lambda r: (r["business_date"],
                              int(r["store_num"]) if r["store_num"].isdigit() else 999,
                              r["item_name"] or "", r["channel"], r["sub_channel"]))
    for ri, r in enumerate(agg, start=1):
        ws.write(ri, 0, r["business_date"])
        ws.write(ri, 1, r["store_num"])
        ws.write(ri, 2, store_names.get(r["store_num"], ""))
        ws.write(ri, 3, r.get("item_name", "") or "")
        ws.write(ri, 4, str(r["item_uuid"]))
        ws.write(ri, 5, fmt_channel(r["channel"]))
        ws.write(ri, 6, fmt_subch(r["sub_channel"]))
        for ci, m in enumerate(METRICS, start=7):
            fmt = int_f if m.endswith("_qty") else money
            ws.write(ri, ci, r.get(m, 0), fmt)
    ws.freeze_panes(1, 7)
    ws.set_column(0, 0, 12); ws.set_column(1, 1, 8); ws.set_column(2, 2, 18)
    ws.set_column(3, 3, 30); ws.set_column(4, 4, 18); ws.set_column(5, 6, 11)
    ws.set_column(7, 6 + len(METRICS), 14)
    return len(agg)


def write_sheet4_variance(wb, curr_rows, prev_rows, curr_period, prev_period, store_names: dict):
    """Sheet 4: 环比分析 — 当期 vs 前推等长区间. 加门店名 + 行类型 (新品/停售/同期对比)."""
    ws = wb.add_worksheet("环比分析")
    hdr = _header_fmt(wb); money = _money_fmt(wb); int_f = _int_fmt(wb); title = _title_fmt(wb)
    pct_fmt = wb.add_format({"num_format": "0.00%"})
    new_f = wb.add_format({"bg_color": "#E2EFDA"})  # 绿: 新品
    gone_f = wb.add_format({"bg_color": "#FCE4D6"})  # 橙: 停售

    ws.merge_range(0, 0, 0, 12, f"环比: 当期 {curr_period} vs 上期 {prev_period}", title)
    cols = [
        "门店编号", "门店名", "商品名", "渠道", "子渠道", "行类型",
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

    curr_map = keymap(curr_rows); prev_map = keymap(prev_rows)
    all_keys = sorted(set(curr_map.keys()) | set(prev_map.keys()),
                      key=lambda k: (int(k[0]) if k[0].isdigit() else 999, k[1], k[2], k[3]))

    ri = 2
    for k in all_keys:
        cur = curr_map.get(k, {}); prv = prev_map.get(k, {})
        item_name = cur.get("item_name") or prv.get("item_name") or ""
        cq = cur.get("qty", 0) or 0; pq = prv.get("qty", 0) or 0
        cr = cur.get("sales_price", 0) or 0; pr = prv.get("sales_price", 0) or 0
        # 行类型 + 整行底色
        if pq == 0 and cq > 0:
            row_type = "🆕 新品"; row_fmt = new_f
        elif cq == 0 and pq > 0:
            row_type = "❌ 停售"; row_fmt = gone_f
        else:
            row_type = "同期对比"; row_fmt = None
        total_diff = cr - pr
        prev_avg_price = (pr / pq) if pq else 0
        volume_var = (cq - pq) * prev_avg_price
        price_var = total_diff - volume_var
        pct = (total_diff / pr) if pr else None

        def W(col, val, fmt=None):
            if row_fmt and fmt is None:
                ws.write(ri, col, val, row_fmt)
            elif fmt is not None:
                ws.write(ri, col, val, fmt)
            else:
                ws.write(ri, col, val)
        W(0, k[0]); W(1, store_names.get(k[0], "")); W(2, item_name)
        W(3, fmt_channel(k[2])); W(4, fmt_subch(k[3])); W(5, row_type)
        W(6, cq, int_f); W(7, pq, int_f)
        W(8, cr, money); W(9, pr, money)
        W(10, total_diff, money); W(11, volume_var, money); W(12, price_var, money)
        if pct is not None:
            W(13, pct, pct_fmt)
        ri += 1
    ws.freeze_panes(2, 6)
    ws.set_column(0, 0, 8); ws.set_column(1, 1, 18); ws.set_column(2, 2, 30)
    ws.set_column(3, 4, 11); ws.set_column(5, 5, 11); ws.set_column(6, 13, 14)
    return ri - 2


def write_meta_sheet(wb, version: int, start, end, prev_start, prev_end, totals: dict):
    ws = wb.add_worksheet("说明")
    title = wb.add_format({"bold": True, "font_color": "red", "font_size": 14})
    section = wb.add_format({"bold": True, "bg_color": "#FFF2CC", "font_size": 11})
    label = wb.add_format({"bold": True})
    wrap = wb.add_format({"text_wrap": True, "valign": "top"})

    ws.write(0, 0, f"区间销售对账报表 — 内部版本 v{version}", title)
    ws.write(2, 0, "当期区间", label); ws.write(2, 1, f"{start} ~ {end}")
    ws.write(3, 0, "上期区间", label); ws.write(3, 1, f"{prev_start} ~ {prev_end}")
    ws.write(4, 0, "生成时间", label); ws.write(4, 1, datetime.now().isoformat(timespec="seconds"))

    r = 6
    ws.merge_range(r, 0, r, 1, "Sheet 行数统计", section); r += 1
    for k, v in totals.items():
        ws.write(r, 0, k); ws.write(r, 1, v); r += 1

    r += 1
    ws.merge_range(r, 0, r, 1, "1. 会计口径声明 (mirror 自 ttpos CountProductSale / RankTakeoutProduct)", section); r += 1
    notes = [
        ("业务日归属", "按结账时间 (complete_time, BKK 时区) 归属当日. 凌晨订单归当前自然日, 不按业务日切分."),
        ("退款归属", "退款数/金额按【原订单日】冲减, 不按退款发生日. 跟 ttpos 后台一致."),
        ("外卖取消", "外卖 order_state=60 取消单独立列 cancelled_* 字段, 不计入正常销售."),
        ("赠送/免单", "免单(free)/赠送(give)发生时实收金额归零, 但销量正常计."),
        ("隐藏单", "本报表不排除 hide_bill_time≠0 的隐藏订单. 跟 ttpos 后台「商品销售」一致, 跟「主统计」可能差 1-3%."),
        ("对账锚监控", "见 sheet「ttpos对账锚」: 自动对比 ttpos 首页营业总览口径与本报表实收. 差异 < 0.01% ✅; 0.01-0.1% ⚠️; > 0.1% 🔴."),
    ]
    for k, v in notes:
        ws.write(r, 0, k, label); ws.write(r, 1, v, wrap); r += 1

    r += 1
    ws.merge_range(r, 0, r, 1, "2. 指标含义解释", section); r += 1
    field_notes = [
        ("销量 (qty)", "实际下单的总件数 (含赠送/免单/退款/取消, 是『毛销量』)."),
        ("营业额 (sales_price)", "订单实际标价 × 销量. 含折扣前金额, 不含税."),
        ("标准金额 (original_amount)", "商品后台标价 × 销量. 临时改价或会员折时跟营业额不同."),
        ("实收金额 (actual_amount)", "ttpos 真实收款 (赠送归零, 扣退款, 用成交价). 这是进账的钱."),
        ("退单数/退单金额", "已结账后退款的件数/金额 (按原订单日)."),
        ("免单数/免单金额 (free)", "整单免单 - 销量正常计, 实收为 0."),
        ("赠送数/赠送金额 (give)", "单品赠送 - 销量正常计, 实收为 0."),
        ("折扣金额 (discount)", "实际成交价低于标价的差额 (营业额 - 实收 - 退款 - 免单 - 赠送)."),
        ("取消数/取消金额 (cancelled)", "外卖 state=60 取消单. 堂食固定为 0."),
    ]
    for k, v in field_notes:
        ws.write(r, 0, k, label); ws.write(r, 1, v, wrap); r += 1

    r += 1
    ws.merge_range(r, 0, r, 1, "3. 渠道/子渠道说明", section); r += 1
    ch_notes = [
        ("堂食 (dine)", "POS 收银台直接结账的订单. 子渠道固定为『POS堂食』."),
        ("外卖 (takeout)", "外卖订单. 按平台拆分: Grab / Lineman / Shopee / FoodPanda / 外卖POS自接(平台为空, 是 POS 上手动开的外卖单)."),
    ]
    for k, v in ch_notes:
        ws.write(r, 0, k, label); ws.write(r, 1, v, wrap); r += 1

    r += 1
    ws.merge_range(r, 0, r, 1, "4. 校验器", section); r += 1
    ws.write(r, 0, "销量恒等式", label)
    ws.write(r, 1, "qty = (qty - 免 - 赠 - 退 - 取消) + 免 + 赠 + 退 + 取消", wrap); r += 1
    ws.write(r, 0, "金额恒等式", label)
    ws.write(r, 1, "营业额 = 实收 + 退单金额 + 免单金额 + 赠送金额 + 折扣金额", wrap); r += 1
    ws.write(r, 0, "对账锚", label)
    ws.write(r, 1, "ttpos 营业总览口径 ≈ 本报表实收 (华莱士现状 byte-equal)", wrap); r += 1

    ws.set_column(0, 0, 20); ws.set_column(1, 1, 90)
    for row_i in range(6, r):
        ws.set_row(row_i, 32)


def build_validator_check_rows(rows) -> list:
    """构建校验器 check_rows (按 store_num, item_uuid, channel 聚合)."""
    check_rows = []
    for r in aggregate(rows, ["store_num", "item_uuid", "channel"]):
        cq = r.get("cancelled_qty", 0) or 0
        rq = r.get("refund_qty", 0) or 0
        fq = r.get("free_qty", 0) or 0
        gq = r.get("give_qty", 0) or 0
        sp = r.get("sales_price", 0) or 0
        ca = r.get("cancelled_amount", 0) or 0
        check_rows.append({
            "store_num": r["store_num"],
            "item_uuid": r["item_uuid"],
            "qty": r.get("qty", 0),
            "net_qty": r.get("qty", 0) - rq - fq - gq - cq,
            "free_qty": fq, "give_qty": gq, "refund_qty": rq, "cancelled_qty": cq,
            "sales_price": sp,
            "revenue": r.get("actual_amount", 0),
            "refund_amount": r.get("refund_amount", 0),
            "free_amount": r.get("free_amount", 0),
            "give_amount": r.get("give_amount", 0),
            "discount_amount": r.get("discount_amount", 0),
            "cancelled_amount": ca,
            # 定义式补齐 (SQL 未投影 gross_amount), 真校验在 sale_event 报表
            "gross_amount": sp + ca,
        })
    return check_rows


def run_validators(rows, label: str):
    """跑销量+金额恒等式 (仅日志/预览用). 按 (store_num, item_uuid) 聚合再 check."""
    log(f"\n=== 校验器: {label} ===")
    check_rows = build_validator_check_rows(rows)
    result = check(check_rows, DEFAULT_IDENTITIES)
    print_result(result, row_label=lambda r: f"店 {r['store_num']} item {r['item_uuid']}")
    if result.has_must_fix:
        log("⚠️  有 🔴 离谱违反.")
    return result


def md5_file(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    sd, ed, stores_filter, workers, ver_override, force = parse_args()
    assert_month_not_frozen(sd.strftime("%Y-%m"))
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

    log("\n[0/2] 查门店名 (BQ ttpos_setting 权威源)...")
    store_names = fetch_store_names(store_list, workers)
    log(f"  拿到 {len(store_names)}/{len(store_list)} 家店名")

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
    totals["区间汇总-长表"] = write_sheet1_period_long(wb, curr_rows, store_names)
    totals["单店汇总"] = write_sheet2_store_pivot(wb, curr_rows, store_names)
    totals["每日明细"] = write_sheet3_daily(wb, curr_rows, store_names)
    totals["环比分析"] = write_sheet4_variance(
        wb, curr_rows, prev_rows,
        curr_period=f"{sd}~{ed}", prev_period=f"{prev_sd}~{prev_ed}",
        store_names=store_names,
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

    # 零容差闸门 — 在 wb.close() 前执行, 有 MUST_FIX 且无 --force 则 exit(2), 不落盘
    from semantic.validators.gate import (
        add_watermark_sheet_xlsxwriter, validate_and_gate)
    from semantic.validators.identities import FULL_IDENTITIES

    gate_rows = build_validator_check_rows(curr_rows)
    outcome = validate_and_gate(
        gate_rows, FULL_IDENTITIES,
        force=force, report_name="sales_period",
        row_label=lambda r: f"店 {r['store_num']} item {r['item_uuid']}",
    )
    if outcome.needs_watermark:
        add_watermark_sheet_xlsxwriter(wb, outcome.watermark_lines())

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
