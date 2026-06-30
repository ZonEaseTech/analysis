#!/usr/bin/env python3
"""查询华莱士(CFG总部)所有门店4月份Grab支付方式使用情况"""
import sys
sys.path.insert(0, '/home/weifashi/hwt/analysis')

from bq_reports.utils.bq_client import get_bq_client

client = get_bq_client()
BQ_LOCATION = "asia-southeast1"

# 2026-04-01 00:00 BKK ~ 2026-05-01 00:00 BKK
START_TS = 1774976400
END_TS = 1777568400

# ========== 1. 从 saas 数据集获取所有CFG总部下的TH门店 ==========
print("=" * 80)
print("从 saas.ttpos_company + saas.ttpos_company_setting 获取华莱士门店")
print("=" * 80)

job = client.query('''
    SELECT c.uuid, c.name, cs.erpnext_company_abbr
    FROM `diyl-407103`.`saas`.`ttpos_company` c
    LEFT JOIN `diyl-407103`.`saas`.`ttpos_company_setting` cs
      ON cs.company_uuid = c.uuid AND cs.delete_time = 0
    WHERE c.delete_time = 0
      AND cs.headquarter_uuid = 5080409448448000
      AND cs.erpnext_company_abbr LIKE 'TH%'
    ORDER BY cs.erpnext_company_abbr
''', location=BQ_LOCATION)

all_th_stores = []
for row in job.result():
    all_th_stores.append({
        'uuid': str(row.uuid),
        'name': row.name,
        'abbr': row.erpnext_company_abbr or '',
        'dataset': f"shop{row.uuid}",
    })

# 检查哪些有BQ数据集
datasets = list(client.list_datasets())
shop_dataset_ids = set(d.dataset_id for d in datasets if d.dataset_id.startswith('shop'))

stores = [s for s in all_th_stores if s['dataset'] in shop_dataset_ids]
no_bq_stores = [s for s in all_th_stores if s['dataset'] not in shop_dataset_ids]

print(f"\nCFG总部下 TH 开头门店总数: {len(all_th_stores)}")
print(f"有 BigQuery 数据的门店: {len(stores)}")
print(f"无 BigQuery 数据的门店: {len(no_bq_stores)}")

if no_bq_stores:
    print(f"\n无BQ数据的门店:")
    for s in no_bq_stores:
        print(f"  {s['abbr']} {s['name']}")

print(f"\n共 {len(stores)} 家有效门店:\n")
for i, s in enumerate(stores, 1):
    print(f"  {i:2}. {s['abbr']} {s['name']}")

# ========== 2. 查询4月份Grab支付方式数据 ==========
print("\n" + "=" * 80)
print("4月份 Grab 支付方式使用情况")
print("=" * 80)

results = []

for s in stores:
    dataset = s['dataset']

    # POS侧 Grab
    pos_rows = []
    try:
        job = client.query(f"""
            SELECT DATE(TIMESTAMP_SECONDS(sp.complete_time), 'Asia/Bangkok') AS pay_date,
                pm.payment_name AS method_name, COUNT(*) AS bill_cnt,
                ROUND(SUM(sp.payment_amount), 2) AS total_amount
            FROM `diyl-407103`.`{dataset}`.`ttpos_statistics_payment` sp
            LEFT JOIN `diyl-407103`.`{dataset}`.`ttpos_payment_method` pm
                ON pm.uuid = sp.payment_method_uuid
            WHERE sp.delete_time = 0 AND sp.complete_time >= {START_TS}
                AND sp.complete_time < {END_TS}
                AND LOWER(IFNULL(pm.payment_name, '')) LIKE '%grab%'
            GROUP BY pay_date, method_name ORDER BY pay_date
        """, location=BQ_LOCATION)
        for row in job.result():
            pos_rows.append({'date': row.pay_date.isoformat() if row.pay_date else None,
                             'method': row.method_name, 'count': row.bill_cnt,
                             'amount': float(row.total_amount) if row.total_amount else 0})
    except Exception:
        pass

    # 外卖平台 Grab
    to_rows = []
    try:
        job = client.query(f"""
            SELECT DATE(TIMESTAMP_SECONDS(
                CASE WHEN order_state = 40 AND completed_time > 0 THEN completed_time
                ELSE accepted_time END), 'Asia/Bangkok') AS pay_date,
                COUNT(*) AS bill_cnt, ROUND(SUM(platform_total), 2) AS total_amount
            FROM `diyl-407103`.`{dataset}`.`ttpos_takeout_order`
            WHERE delete_time = 0 AND order_state IN (10, 20, 30, 40)
                AND platform = 'grab' AND accepted_time > 0
                AND (CASE WHEN order_state = 40 AND completed_time > 0 THEN completed_time
                    ELSE accepted_time END) >= {START_TS}
                AND (CASE WHEN order_state = 40 AND completed_time > 0 THEN completed_time
                    ELSE accepted_time END) < {END_TS}
            GROUP BY pay_date ORDER BY pay_date
        """, location=BQ_LOCATION)
        for row in job.result():
            to_rows.append({'date': row.pay_date.isoformat() if row.pay_date else None,
                            'count': row.bill_cnt,
                            'amount': float(row.total_amount) if row.total_amount else 0})
    except Exception:
        pass

    if pos_rows or to_rows:
        results.append({'store': s, 'pos': pos_rows, 'takeout': to_rows})

# ========== 3. 输出结果 ==========
print(f"\n共有 {len(results)} 家门店在4月份有 Grab 相关数据:\n")

# 按是否有POS Grab排序
pos_results = [r for r in results if r['pos']]
to_only_results = [r for r in results if r['takeout'] and not r['pos']]

print(f"\n一、有POS侧 Grab 支付的门店: {len(pos_results)} 家")
print("-" * 80)
for r in pos_results:
    s = r['store']
    total_cnt = sum(d['count'] for d in r['pos'])
    total_amt = sum(d['amount'] for d in r['pos'])
    print(f"  {s['abbr']} {s['name']:<45} {total_cnt:>4}笔  ¥{total_amt:>10,.2f}")

print(f"\n二、只有外卖平台 Grab 的门店: {len(to_only_results)} 家")
print("-" * 80)
for r in to_only_results:
    s = r['store']
    total_cnt = sum(d['count'] for d in r['takeout'])
    total_amt = sum(d['amount'] for d in r['takeout'])
    print(f"  {s['abbr']} {s['name']:<45} {total_cnt:>4}笔  ¥{total_amt:>10,.2f}")

# 没有Grab数据的门店
no_grab = [s for s in stores if s['name'] not in [r['store']['name'] for r in results]]
print(f"\n三、没有Grab相关数据的门店: {len(no_grab)} 家")
print("-" * 80)
for s in no_grab:
    print(f"  {s['abbr']} {s['name']}")

# 日期汇总
all_pos_dates = sorted(set(d['date'] for r in results for d in r['pos']))
all_to_dates = sorted(set(d['date'] for r in results for d in r['takeout']))

print(f"\n四、POS侧 Grab 支付有数据的日期: {len(all_pos_dates)} 天")
print("-" * 80)
for d in all_pos_dates:
    day_cnt = sum(x['count'] for r in results for x in r['pos'] if x['date'] == d)
    day_amt = sum(x['amount'] for r in results for x in r['pos'] if x['date'] == d)
    day_stores = len([r for r in results if any(x['date'] == d for x in r['pos'])])
    print(f"  {d}  {day_stores:>2}家门店  {day_cnt:>4}笔  ¥{day_amt:>12,.2f}")

print(f"\n五、外卖平台 Grab 有数据的日期: {len(all_to_dates)} 天")
print("-" * 80)
for d in all_to_dates:
    day_cnt = sum(x['count'] for r in results for x in r['takeout'] if x['date'] == d)
    day_amt = sum(x['amount'] for r in results for x in r['takeout'] if x['date'] == d)
    day_stores = len([r for r in results if any(x['date'] == d for x in r['takeout'])])
    print(f"  {d}  {day_stores:>2}家门店  {day_cnt:>5}笔  ¥{day_amt:>12,.2f}")

# 总计
total_pos_cnt = sum(d['count'] for r in results for d in r['pos'])
total_pos_amt = sum(d['amount'] for r in results for d in r['pos'])
total_to_cnt = sum(d['count'] for r in results for d in r['takeout'])
total_to_amt = sum(d['amount'] for r in results for d in r['takeout'])

print(f"\n" + "=" * 80)
print("六、总计")
print("=" * 80)
print(f"  POS侧 Grab:        {total_pos_cnt:>6}笔  ¥{total_pos_amt:>14,.2f}")
print(f"  外卖平台 Grab:     {total_to_cnt:>6}笔  ¥{total_to_amt:>14,.2f}")
print(f"  合计:              {total_pos_cnt + total_to_cnt:>6}笔  ¥{total_pos_amt + total_to_amt:>14,.2f}")
