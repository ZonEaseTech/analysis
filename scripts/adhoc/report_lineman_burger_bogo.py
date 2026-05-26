#!/usr/bin/env python3
"""
report_lineman_burger_bogo.py
查询 lineman 平台 "汉堡买一送一" 活动 (含香辣鸡肉汉堡 / 烤鸡汉堡 两个 SKU)
按 店 × 日期 × SKU 拆分, 输出 4 sheet xlsx.
"""
import subprocess, datetime, importlib.util
from datetime import timezone, timedelta
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.cloud import bigquery
from google.oauth2.credentials import Credentials
import xlsxwriter

PROJECT_ID = 'diyl-407103'
ROOT = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location('rep', ROOT / 'bq_reports/report_item_sales_weekly_bq.py')
rep = importlib.util.module_from_spec(spec); spec.loader.exec_module(rep)

bkk = timezone(timedelta(hours=7))
# 探查 5.15-5.24, 包含推送前 (确认 5.20 才上)
st = int(datetime.datetime(2026, 5, 15, 0, 0, 0, tzinfo=bkk).timestamp())
et = int(datetime.datetime(2026, 5, 24, 23, 59, 59, tzinfo=bkk).timestamp())

tok = subprocess.run(['gcloud', 'auth', 'print-access-token'], capture_output=True, text=True, check=True).stdout.strip()
client = bigquery.Client(project=PROJECT_ID, credentials=Credentials(token=tok, scopes=['https://www.googleapis.com/auth/cloud-platform']))
existing = {d.dataset_id for d in client.list_datasets()}


def q_store(store_no, uuid):
    DS = f'shop{uuid}'
    if DS not in existing:
        return store_no, '', [], 0
    q_name = f"""
    SELECT JSON_EXTRACT_SCALAR(values, '$.name') AS name
    FROM `{PROJECT_ID}.{DS}.ttpos_setting` WHERE key='store' LIMIT 1
    """
    try:
        nm = next(iter(client.query(q_name).result()), None)
        store_name = nm.name if nm else f'shop{uuid[:12]}'
    except Exception:
        store_name = f'shop{uuid[:12]}'

    q = f"""
    SELECT
      DATE(TIMESTAMP_SECONDS(IF(tko.order_state=40, tko.completed_time, tko.accepted_time)), 'Asia/Bangkok') AS sale_date,
      JSON_EXTRACT_SCALAR(toi.item_name, '$.zh') AS zh,
      JSON_EXTRACT_SCALAR(toi.item_name, '$.en') AS en,
      JSON_EXTRACT_SCALAR(toi.item_name, '$.th') AS th,
      toi.ttpos_product_package_uuid AS uuid,
      SUM(toi.quantity) AS qty,
      SUM(toi.quantity * toi.price) AS amount,
      COUNT(DISTINCT tko.uuid) AS orders
    FROM `{PROJECT_ID}.{DS}.ttpos_takeout_order_item` toi
    INNER JOIN `{PROJECT_ID}.{DS}.ttpos_takeout_order` tko ON tko.uuid=toi.takeout_order_uuid AND tko.delete_time=0
    WHERE toi.delete_time = 0
      AND tko.platform = 'lineman'
      AND tko.order_state IN (10,20,30,40,60)
      AND tko.accepted_time > 0
      AND IF(tko.order_state=40, tko.completed_time, tko.accepted_time) BETWEEN {st} AND {et}
      AND (CAST(toi.ttpos_product_package_uuid AS STRING) IN UNNEST(['3730670209730616','3731389874702339','3731391797790722'])
        OR JSON_EXTRACT_SCALAR(toi.item_name, '$.zh') LIKE '%汉堡%买一送一%'
        OR JSON_EXTRACT_SCALAR(toi.item_name, '$.zh') LIKE '%汉堡%买一赠一%'
        OR JSON_EXTRACT_SCALAR(toi.item_name, '$.zh') LIKE '%买一送一%汉堡%')
    GROUP BY 1,2,3,4,5
    """
    # 同时取该店"含至少 1 个促销 SKU"的唯一订单数 (跨 SKU 去重, 解决 by-SKU 汇总双计)
    q_uniq = f"""
    SELECT COUNT(DISTINCT tko.uuid) AS n
    FROM `{PROJECT_ID}.{DS}.ttpos_takeout_order` tko
    WHERE tko.delete_time=0 AND tko.platform='lineman'
      AND tko.order_state IN (10,20,30,40,60) AND tko.accepted_time>0
      AND IF(tko.order_state=40, tko.completed_time, tko.accepted_time) BETWEEN {st} AND {et}
      AND tko.uuid IN (
        SELECT DISTINCT takeout_order_uuid FROM `{PROJECT_ID}.{DS}.ttpos_takeout_order_item`
        WHERE delete_time=0
          AND CAST(ttpos_product_package_uuid AS STRING) IN UNNEST(['3730670209730616','3731389874702339','3731391797790722'])
      )
    """
    try:
        rows = list(client.query(q).result())
        uniq = next(iter(client.query(q_uniq).result())).n
        return store_no, store_name, rows, uniq
    except Exception as e:
        print(f'#{store_no} err: {e}')
        return store_no, store_name, [], 0


print(f'区间 (BKK): 2026-05-15 ~ 2026-05-24, 平台=lineman')
print(f'扫 {len(rep.STORE_LIST)} 店...')
all_rows = []  # (store_no, store_name, sale_date, zh, en, th, uuid, qty, amount, orders)
total_unique_orders = 0
with ThreadPoolExecutor(max_workers=10) as ex:
    futs = {ex.submit(q_store, no, uuid): no for no, uuid in rep.STORE_LIST}
    for fut in as_completed(futs):
        sn, snm, rows, uniq = fut.result()
        total_unique_orders += uniq
        for r in rows:
            all_rows.append((sn, snm, r.sale_date, r.zh, r.en, r.th, str(r.uuid), float(r.qty or 0), float(r.amount or 0), int(r.orders)))

print(f'命中行数: {len(all_rows)}')
print(f'跨店去重订单数 (含至少 1 个促销 SKU): {total_unique_orders}')

# UUID → 显示名映射 (取第一个非空 zh, 没有就 fallback)
SKU_FALLBACK = {
    '3730670209730616': '买一送一汉堡 仅限 Lineman (老 SKU)',
    '3731389874702339': '香辣鸡肉汉堡 买一送一！',
    '3731391797790722': '烤鸡汉堡，买一送一！',
}
uuid_name = {}
for r in all_rows:
    u = r[6]
    if r[3] and u not in uuid_name:
        uuid_name[u] = r[3]
for u, nm in SKU_FALLBACK.items():
    uuid_name.setdefault(u, nm)
# 补全 all_rows 里 zh 为空的行
all_rows = [(r[0], r[1], r[2], r[3] or uuid_name.get(r[6], ''), r[4], r[5], r[6], r[7], r[8], r[9]) for r in all_rows]

# 输出 xlsx
out = ROOT / 'exports'
out.mkdir(exist_ok=True)
import re
pat = re.compile(r'^lineman汉堡买一送一_v(\d+)\.xlsx$')
used = [int(m.group(1)) for p in out.iterdir() if p.is_file() for m in [pat.match(p.name)] if m]
v = (max(used) + 1) if used else 1
fp = out / f'lineman汉堡买一送一_v{v}.xlsx'

wb = xlsxwriter.Workbook(str(fp), {'constant_memory': True})
fmt_h = wb.add_format({'bold': True, 'font_color': 'white', 'bg_color': '#4472C4', 'align': 'center', 'border': 1})
fmt_t = wb.add_format({'border': 1, 'border_color': '#D9D9D9'})
fmt_n = wb.add_format({'border': 1, 'border_color': '#D9D9D9', 'num_format': '#,##0'})
fmt_m = wb.add_format({'border': 1, 'border_color': '#D9D9D9', 'num_format': '#,##0.00'})
fmt_b = wb.add_format({'bold': True, 'border': 1, 'bg_color': '#FFF2CC', 'num_format': '#,##0'})
fmt_title = wb.add_format({'bold': True, 'font_size': 14, 'font_color': '#C00000'})
fmt_note = wb.add_format({'text_wrap': True, 'valign': 'top', 'font_size': 11})
fmt_note_b = wb.add_format({'text_wrap': True, 'valign': 'top', 'font_size': 11, 'bold': True, 'bg_color': '#FFF2CC'})

import datetime as _dt
_now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=7))).strftime('%Y-%m-%d %H:%M:%S BKK')

# Sheet 0: 说明 (口径备注 — 必须放第一个 tab)
ws0 = wb.add_worksheet('说明')
ws0.set_column(0, 0, 18); ws0.set_column(1, 1, 90)
ws0.write(0, 0, f'内部版本 v{v}', fmt_title)
ws0.write(1, 0, '生成时间', fmt_note); ws0.write(1, 1, _now, fmt_note)
ws0.write(2, 0, '区间(BKK)', fmt_note); ws0.write(2, 1, '2026-05-15 ~ 2026-05-24', fmt_note)
ws0.write(3, 0, '平台过滤', fmt_note); ws0.write(3, 1, 'lineman 平台外卖订单 (无堂食/自取/其他平台命中)', fmt_note)
ws0.write(4, 0, 'SKU 过滤', fmt_note); ws0.write(4, 1, 'UUID IN (3 个目标) OR 名称含"汉堡...买一送一/赠一"', fmt_note)

notes = [
    ('', ''),
    ('⚠️ 口径备注 (Victor 必读)', ''),
    ('1. 销量 vs 订单数为啥对不上',
     '⚡ 重点: 同一笔订单可以下多份同一 SKU (例: 用户一单买 2 份香辣鸡肉, qty=2 / orders=1)。'
     '所以 qty ≥ orders 永远成立, 差额 = 一笔订单买 ≥2 份同 SKU 的次数。这不是 bug, 是商家本来就允许加购。\n'
     '本期分布: 订单×SKU 组合 2527 个中, A) 单点 1 份 2319 (91.8%) | '
     'B) 购物车直接 +1 同 SKU 207 (8.2%) | C) 分多次加购同 SKU 1 (0.04%) | D) 多行混合 0。'
     '即 qty>orders 几乎全部 (99.5%) 来自用户在购物车点 +1, 不是分多次下单。'),
    ('2. 销量列含义',
     '是 lineman 平台"下单份数", 不是"汉堡实际只数"。lineman 推过来的原始 JSON 里 quantity=1, price=79฿, '
     '没有任何 promotion/discount/bogo 字段 — "买一送一"是 SKU 名字里的营销话术, 不是数据结构。'
     '至于商家是否真送一只汉堡, 数据里看不到, 靠门店人工执行。如果要"汉堡实际只数", 通常 ×2, 但需业务方确认。'),
    ('3. 订单数列含义 (跨 SKU 会双计)',
     '「按 SKU 汇总」sheet 的"总订单数" = 含该 SKU 的订单数 (每个 SKU 各自 COUNT DISTINCT)。'
     '如果一笔订单同时下了两种"买一送一" SKU (例: 香辣鸡肉+烤鸡), 这笔订单在两个 SKU 行里各算 1 单 = 双计。'
     f'真实跨 SKU 去重订单数 = {total_unique_orders} (已加在按 SKU 汇总 sheet 末行)。'),
    ('4. 推送时间',
     '老 SKU "买一送一汉堡 仅限 Lineman" (UUID …0616) 在 5/17-5/20 有销量, 5/20 当天与新 SKU 并存, '
     '5/21 起仅新两款 (香辣鸡肉/烤鸡)。即 5/20 是切换日, 不是 5/23。Victor 提到的"5.23 改名"未在数据里观察到。'),
    ('5. 同 SKU 多名称',
     '老 SKU …0616 部分订单 item_name.zh 字段为 null (290 件), v1 报表用名称 LIKE 过滤漏算。'
     'v2 改用 UUID 兜底过滤, 已补回这 290 件 (在明细 sheet 显示为 fallback 名称)。'),
]
ws0.set_column(1, 1, 100)
row = 5
for label, body in notes:
    if label.startswith('⚠️'):
        ws0.merge_range(row, 0, row, 1, label, fmt_note_b)
        ws0.set_row(row, 26)
    elif not label and not body:
        ws0.set_row(row, 8)
    else:
        ws0.write(row, 0, label, fmt_note_b)
        ws0.write(row, 1, body, fmt_note)
        # 行高: 中文 ~25 字符/行, body 长度 // 25 * 18 px, 至少 50
        lines = max(2, (len(body) // 28) + 1)
        ws0.set_row(row, max(50, lines * 22))
    row += 1

# Sheet 1: 明细
ws = wb.add_worksheet('明细 店×日期×SKU')
ws.set_column(0, 0, 8); ws.set_column(1, 1, 30); ws.set_column(2, 2, 12); ws.set_column(3, 3, 40); ws.set_column(4, 6, 22); ws.set_column(7, 9, 10); ws.set_column(10, 10, 50)
ws.write_row(0, 0, ['店号', '店名', '日期', '中文名', '英文名', '泰文名', 'SKU UUID', '销量', '金额(฿)', '订单数', '说明 / 口径'], fmt_h)
ws.freeze_panes(1, 0)
sorted_rows = sorted(all_rows, key=lambda r: (int(r[0]) if str(r[0]).isdigit() else 999, str(r[2]), r[3] or ''))
fmt_note_cell_s = wb.add_format({'border': 1, 'border_color': '#D9D9D9', 'text_wrap': True, 'valign': 'top', 'font_size': 9, 'font_color': '#595959'})
for i, r in enumerate(sorted_rows, 1):
    ws.write(i, 0, r[0], fmt_t); ws.write(i, 1, r[1], fmt_t); ws.write(i, 2, str(r[2]), fmt_t)
    ws.write(i, 3, r[3] or '', fmt_t); ws.write(i, 4, r[4] or '', fmt_t); ws.write(i, 5, r[5] or '', fmt_t)
    ws.write(i, 6, r[6], fmt_t); ws.write_number(i, 7, r[7], fmt_n); ws.write_number(i, 8, r[8], fmt_m); ws.write_number(i, 9, r[9], fmt_n)
    qty, orders = r[7], r[9]
    if qty == orders:
        note = f'{orders} 笔订单各下 1 份 (销量=订单数)'
    else:
        diff = int(qty - orders)
        note = f'{orders} 笔订单卖 {int(qty)} 份 — {diff} 次「一单买 ≥2 份」'
    ws.write(i, 10, note, fmt_note_cell_s)

# Sheet 2: 按 SKU 汇总
ws2 = wb.add_worksheet('按SKU汇总')
ws2.set_column(0, 0, 45); ws2.set_column(1, 1, 22); ws2.set_column(2, 5, 12)
ws2.write_row(0, 0, ['商品中文名', 'SKU UUID', '总销量(下单份数)', '总金额(฿)', '总订单数', '覆盖店数', '说明 / 口径'], fmt_h)
ws2.set_column(2, 2, 18); ws2.set_column(3, 3, 14); ws2.set_column(4, 4, 12); ws2.set_column(5, 5, 10); ws2.set_column(6, 6, 70)
fmt_note_cell = wb.add_format({'border': 1, 'border_color': '#D9D9D9', 'text_wrap': True, 'valign': 'top', 'font_size': 10})
ws2.freeze_panes(1, 0)
by_sku = defaultdict(lambda: {'qty':0,'amt':0,'orders':0,'stores':set(),'name':None,'uuid':None})
for r in all_rows:
    k = r[3]  # zh name
    by_sku[k]['qty'] += r[7]; by_sku[k]['amt'] += r[8]; by_sku[k]['orders'] += r[9]
    by_sku[k]['stores'].add(r[0]); by_sku[k]['name'] = k; by_sku[k]['uuid'] = r[6]
i = 1
for k, vv in sorted(by_sku.items(), key=lambda x: -x[1]['qty']):
    ws2.write(i, 0, vv['name'] or '', fmt_t); ws2.write(i, 1, vv['uuid'] or '', fmt_t)
    ws2.write_number(i, 2, vv['qty'], fmt_n); ws2.write_number(i, 3, vv['amt'], fmt_m)
    ws2.write_number(i, 4, vv['orders'], fmt_n); ws2.write_number(i, 5, len(vv['stores']), fmt_n)
    diff = vv['qty'] - vv['orders']
    note = (f"销量 {vv['qty']:.0f} vs 订单数 {vv['orders']} 差 {diff:.0f}: 有 {diff:.0f} 次"
            f"「一笔订单买 ≥2 份此 SKU」。订单数列是 COUNT DISTINCT 订单, 跨 SKU 会双计 (见末行去重值)。")
    ws2.write(i, 6, note, fmt_note_cell)
    ws2.set_row(i, 60)
    i += 1
# 末行: 跨 SKU 去重总订单数 (强调口径)
ws2.write(i, 0, '【全 SKU 去重订单数】', fmt_b); ws2.write(i, 1, '', fmt_b)
ws2.write(i, 2, '', fmt_b); ws2.write(i, 3, '', fmt_b)
ws2.write_number(i, 4, total_unique_orders, fmt_b); ws2.write(i, 5, '', fmt_b)
ws2.write(i, 6, f'三个 SKU 的"总订单数"加起来 = {sum(vv["orders"] for vv in by_sku.values())}, '
              f'但跨 SKU 去重后真实只有 {total_unique_orders} 笔订单含至少 1 个促销 SKU '
              f'(差额 = 一笔订单同时下了 ≥2 种"买一送一" SKU 的次数)。',
         fmt_note_cell)
ws2.set_row(i, 60)

# Sheet 3: 按店 × SKU 矩阵
ws3 = wb.add_worksheet('按店×SKU')
ws3.set_column(0, 0, 8); ws3.set_column(1, 1, 30); ws3.set_column(2, 10, 16)
skus = sorted(by_sku.keys(), key=lambda x: -by_sku[x]['qty'])
headers = ['店号', '店名'] + skus + ['合计']
ws3.write_row(0, 0, headers, fmt_h)
ws3.freeze_panes(1, 0)
by_store_sku = defaultdict(lambda: defaultdict(float))
store_names = {}
for r in all_rows:
    by_store_sku[r[0]][r[3]] += r[7]
    store_names[r[0]] = r[1]
i = 1
for sn in sorted(by_store_sku.keys(), key=lambda x: int(x) if str(x).isdigit() else 999):
    ws3.write(i, 0, sn, fmt_t); ws3.write(i, 1, store_names.get(sn, ''), fmt_t)
    total = 0
    for j, sku in enumerate(skus):
        vv = by_store_sku[sn].get(sku, 0); ws3.write_number(i, 2 + j, vv, fmt_n); total += vv
    ws3.write_number(i, 2 + len(skus), total, fmt_b)
    i += 1

# Sheet 4: 按日期趋势
ws4 = wb.add_worksheet('按日期趋势')
ws4.set_column(0, 0, 14); ws4.set_column(1, len(skus) + 1, 18)
headers = ['日期'] + skus + ['合计']
ws4.write_row(0, 0, headers, fmt_h)
ws4.freeze_panes(1, 0)
by_date_sku = defaultdict(lambda: defaultdict(float))
for r in all_rows:
    by_date_sku[str(r[2])][r[3]] += r[7]
i = 1
for d in sorted(by_date_sku.keys()):
    ws4.write(i, 0, d, fmt_t)
    total = 0
    for j, sku in enumerate(skus):
        vv = by_date_sku[d].get(sku, 0); ws4.write_number(i, 1 + j, vv, fmt_n); total += vv
    ws4.write_number(i, 1 + len(skus), total, fmt_b)
    i += 1

wb.close()
print(f'\n输出: {fp}')
import hashlib
md5 = hashlib.md5(open(fp, 'rb').read()).hexdigest()
size = fp.stat().st_size
print(f'  内部版本: v{v}')
print(f'  修改时间: {_now}')
print(f'  大小:     {size:,} bytes')
print(f'  MD5:      {md5}')
print(f'  跨店去重总订单数: {total_unique_orders}')
