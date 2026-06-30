#!/usr/bin/env python3
"""
audit_subitem_inflation.py
对 report_item_sales_weekly_bq.py 的 dine_subitem 公式做差异审计:
  buggy  = num * copy_num * IF(unit_num=0,1,unit_num)
  fixed  = COALESCE(NULLIF(copy_num,0), NULLIF(unit_num,0), num)   # 对齐 ttpos statistics.go:getSubItemNum

输出: exports/audit_subitem_inflation_<YYYYMM>.xlsx
  Sheet 1 月度汇总: 店 / 套餐子品总行数 / buggy qty / fixed qty / 虚增倍数 / 虚增金额
  Sheet 2 Top SKU: 按虚增 qty 排序 top 50
"""
import argparse, subprocess, datetime, calendar
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from collections import defaultdict
from google.cloud import bigquery
from google.oauth2.credentials import Credentials
from openpyxl import Workbook

PROJECT_ID = 'diyl-407103'
OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / 'exports'

# 复用主报表的门店列表
import importlib.util
spec = importlib.util.spec_from_file_location('rep', Path(__file__).resolve().parent.parent.parent / 'bq_reports/report_item_sales_weekly_bq.py')
rep = importlib.util.module_from_spec(spec); spec.loader.exec_module(rep)
STORE_LIST = rep.STORE_LIST


def get_client():
    tok = subprocess.run(['gcloud','auth','print-access-token'],capture_output=True,text=True,check=True).stdout.strip()
    return bigquery.Client(project=PROJECT_ID, credentials=Credentials(token=tok, scopes=['https://www.googleapis.com/auth/cloud-platform']))


def month_range(yyyymm: str):
    y, m = int(yyyymm[:4]), int(yyyymm[4:6])
    sd = datetime.date(y, m, 1)
    ed = datetime.date(y, m, calendar.monthrange(y, m)[1])
    return sd, ed, int(datetime.datetime(y,m,1,0,0,0).timestamp()), int(datetime.datetime(y,m,ed.day,23,59,59).timestamp())


def query_store(client, store_num, dataset, st, et):
    pp_en = "JSON_EXTRACT_SCALAR(pp.name,'$.en')"
    pp_zh = "JSON_EXTRACT_SCALAR(pp.name,'$.zh')"
    q = f"""
    SELECT
      sop.product_package_uuid AS uuid,
      COALESCE({pp_zh}, {pp_en}) AS name,
      SUM(sop.num * sop.copy_num * IF(sop.unit_num=0,1,sop.unit_num)) AS buggy_qty,
      SUM(COALESCE(NULLIF(sop.copy_num,0), NULLIF(sop.unit_num,0), sop.num)) AS fixed_qty,
      SUM(COALESCE(sop.price,0) * sop.num * sop.copy_num * IF(sop.unit_num=0,1,sop.unit_num)) AS buggy_amt,
      SUM(COALESCE(sop.price,0) * COALESCE(NULLIF(sop.copy_num,0), NULLIF(sop.unit_num,0), sop.num)) AS fixed_amt,
      COUNT(*) AS row_cnt
    FROM `{PROJECT_ID}.{dataset}.ttpos_sale_order_product` sop
    INNER JOIN `{PROJECT_ID}.{dataset}.ttpos_sale_bill` sb
      ON sb.uuid = sop.sale_bill_uuid AND sb.delete_time = 0
    LEFT JOIN `{PROJECT_ID}.{dataset}.ttpos_sale_order` so
      ON so.uuid = sop.sale_order_uuid AND so.delete_time = 0
    LEFT JOIN `{PROJECT_ID}.{dataset}.ttpos_product_package` pp
      ON pp.uuid = sop.product_package_uuid AND pp.delete_time = 0
    WHERE sop.delete_time = 0 AND sop.cancel_time = 0 AND sb.status = 1
      AND sop.product_type = 2
      AND COALESCE(NULLIF(sb.finish_time,0), so.finish_time) BETWEEN {st} AND {et}
    GROUP BY uuid, name
    """
    try:
        return [(store_num, r.uuid, r.name, float(r.buggy_qty or 0), float(r.fixed_qty or 0),
                 float(r.buggy_amt or 0), float(r.fixed_amt or 0), int(r.row_cnt)) for r in client.query(q).result()]
    except Exception as e:
        print(f'  ⚠ #{store_num} {dataset} 查询失败: {e}')
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--month', required=True, help='YYYYMM, 例如 202605')
    ap.add_argument('--workers', type=int, default=8)
    args = ap.parse_args()

    sd, ed, st, et = month_range(args.month)
    print(f'区间 {sd} ~ {ed}  ts {st}~{et}')

    client = get_client()
    existing = {d.dataset_id for d in client.list_datasets()}

    rows_all = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {}
        for no, uuid in STORE_LIST:
            ds = f'shop{uuid}'
            if ds not in existing:
                print(f'  跳过 #{no}: dataset 不存在')
                continue
            futs[ex.submit(query_store, client, no, ds, st, et)] = no
        for fut in as_completed(futs):
            rows = fut.result()
            rows_all.extend(rows)
            print(f'  #{futs[fut]}: {len(rows)} SKU')

    # 汇总
    per_store = defaultdict(lambda: {'buggy_qty':0,'fixed_qty':0,'buggy_amt':0,'fixed_amt':0,'rows':0,'sku':0})
    per_sku = defaultdict(lambda: {'buggy_qty':0,'fixed_qty':0,'buggy_amt':0,'fixed_amt':0,'stores':set(),'name':None})
    for sn, uuid, name, bq, fq, ba, fa, rc in rows_all:
        s = per_store[sn]
        s['buggy_qty']+=bq; s['fixed_qty']+=fq; s['buggy_amt']+=ba; s['fixed_amt']+=fa
        s['rows']+=rc; s['sku']+=1
        k = per_sku[uuid]
        k['buggy_qty']+=bq; k['fixed_qty']+=fq; k['buggy_amt']+=ba; k['fixed_amt']+=fa
        k['stores'].add(sn); k['name']=k['name'] or name

    # Excel 输出
    wb = Workbook()
    ws = wb.active; ws.title='月度汇总'
    ws.append(['门店', 'SKU 数', '套餐子品行数', 'buggy 数量', 'fixed 数量', '虚增数量', '虚增倍数', 'buggy 金额', 'fixed 金额', '虚增金额'])
    tot = {'buggy_qty':0,'fixed_qty':0,'buggy_amt':0,'fixed_amt':0,'rows':0,'sku':0}
    for sn in sorted(per_store.keys(), key=lambda x:int(x)):
        s = per_store[sn]
        inflate_qty = s['buggy_qty'] - s['fixed_qty']
        ratio = s['buggy_qty']/s['fixed_qty'] if s['fixed_qty'] else 0
        inflate_amt = s['buggy_amt'] - s['fixed_amt']
        ws.append([sn, s['sku'], s['rows'], round(s['buggy_qty'],1), round(s['fixed_qty'],1),
                   round(inflate_qty,1), round(ratio,3), round(s['buggy_amt'],1),
                   round(s['fixed_amt'],1), round(inflate_amt,1)])
        for k in tot: tot[k]+=s[k]
    ws.append(['合计', tot['sku'], tot['rows'], round(tot['buggy_qty'],1), round(tot['fixed_qty'],1),
               round(tot['buggy_qty']-tot['fixed_qty'],1),
               round(tot['buggy_qty']/tot['fixed_qty'] if tot['fixed_qty'] else 0,3),
               round(tot['buggy_amt'],1), round(tot['fixed_amt'],1),
               round(tot['buggy_amt']-tot['fixed_amt'],1)])

    ws2 = wb.create_sheet('Top 50 SKU')
    ws2.append(['UUID', '商品', '出现店数', 'buggy 数量', 'fixed 数量', '虚增数量', '虚增倍数', 'buggy 金额', 'fixed 金额', '虚增金额'])
    top = sorted(per_sku.items(), key=lambda x: x[1]['buggy_qty']-x[1]['fixed_qty'], reverse=True)[:50]
    for uuid, k in top:
        ratio = k['buggy_qty']/k['fixed_qty'] if k['fixed_qty'] else 0
        ws2.append([uuid, k['name'] or '(无名)', len(k['stores']), round(k['buggy_qty'],1),
                    round(k['fixed_qty'],1), round(k['buggy_qty']-k['fixed_qty'],1),
                    round(ratio,3), round(k['buggy_amt'],1), round(k['fixed_amt'],1),
                    round(k['buggy_amt']-k['fixed_amt'],1)])

    out = OUTPUT_DIR / f'audit_subitem_inflation_{args.month}.xlsx'
    wb.save(out)
    print(f'\n输出: {out}')
    print(f'  总虚增数量: {tot["buggy_qty"]-tot["fixed_qty"]:,.0f}  (buggy {tot["buggy_qty"]:,.0f} vs fixed {tot["fixed_qty"]:,.0f})')
    print(f'  虚增倍数:   {tot["buggy_qty"]/tot["fixed_qty"] if tot["fixed_qty"] else 0:.3f}x')
    print(f'  总虚增金额: {tot["buggy_amt"]-tot["fixed_amt"]:,.0f} ฿  (buggy {tot["buggy_amt"]:,.0f} vs fixed {tot["fixed_amt"]:,.0f})')


if __name__ == '__main__':
    main()
