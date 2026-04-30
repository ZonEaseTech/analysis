#!/usr/bin/env python3
"""查询华莱士门店4月份Grab支付方式使用情况 - 详细报告"""
import sys
sys.path.insert(0, '/home/weifashi/hwt/analysis')

from bq_reports.utils.bq_client import get_bq_client, STORE_UUIDS

client = get_bq_client()
BQ_LOCATION = "asia-southeast1"

# 2026-04-01 00:00 BKK ~ 2026-05-01 00:00 BKK
START_TS = 1774976400
END_TS = 1777568400

stores = []
for store_uuid in STORE_UUIDS:
    dataset = f"shop{store_uuid}"
    try:
        job = client.query(
            f"""SELECT c.name AS store_name,
                IFNULL((SELECT JSON_EXTRACT_SCALAR(`values`, '$.store_code')
                    FROM `diyl-407103`.`{dataset}`.`ttpos_setting`
                    WHERE `key` = 'store' AND delete_time = 0 LIMIT 1), '') AS store_code
            FROM `diyl-407103`.`{dataset}`.`ttpos_company` c
            WHERE c.delete_time = 0 LIMIT 1""",
            location=BQ_LOCATION
        )
        rows = list(job.result())
        if rows:
            stores.append({'uuid': store_uuid, 'name': rows[0].store_name,
                           'code': rows[0].store_code, 'dataset': dataset})
    except Exception:
        pass

# 收集所有数据
results = []
for s in stores:
    dataset = s['dataset']

    # POS Grab
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
            GROUP BY pay_date, method_name ORDER BY pay_date""", location=BQ_LOCATION)
        for row in job.result():
            pos_rows.append({'date': row.pay_date.isoformat() if row.pay_date else None,
                             'method': row.method_name, 'count': row.bill_cnt,
                             'amount': float(row.total_amount) if row.total_amount else 0})
    except Exception:
        pass

    # 外卖 Grab
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
            GROUP BY pay_date ORDER BY pay_date""", location=BQ_LOCATION)
        for row in job.result():
            to_rows.append({'date': row.pay_date.isoformat() if row.pay_date else None,
                            'count': row.bill_cnt,
                            'amount': float(row.total_amount) if row.total_amount else 0})
    except Exception:
        pass

    if pos_rows or to_rows:
        results.append({'store': s, 'pos': pos_rows, 'takeout': to_rows})

# ===================== 报告输出 =====================
print("=" * 80)
print("华莱士 53 家门店 - 2026年4月 Grab 支付方式使用情况报告")
print("=" * 80)

# 1. 有POS侧Grab支付的门店
pos_stores = [r for r in results if r['pos']]
print(f"\n一、有POS侧 Grab 支付的门店: {len(pos_stores)} 家")
print("-" * 80)
for r in pos_stores:
    s = r['store']
    total_cnt = sum(d['count'] for d in r['pos'])
    total_amt = sum(d['amount'] for d in r['pos'])
    print(f"  {s['code']:>4} {s['name']:<45} {total_cnt:>4}笔  ¥{total_amt:>10,.2f}")

# 2. 只有外卖平台Grab的门店
to_only = [r for r in results if r['takeout'] and not r['pos']]
print(f"\n二、只有外卖平台 Grab 的门店: {len(to_only)} 家")
print("-" * 80)
for r in to_only:
    s = r['store']
    total_cnt = sum(d['count'] for d in r['takeout'])
    total_amt = sum(d['amount'] for d in r['takeout'])
    print(f"  {s['code']:>4} {s['name']:<45} {total_cnt:>4}笔  ¥{total_amt:>10,.2f}")

# 3. 日期汇总
all_pos_dates = sorted(set(d['date'] for r in results for d in r['pos']))
all_to_dates = sorted(set(d['date'] for r in results for d in r['takeout']))

print(f"\n三、POS侧 Grab 支付有数据的日期: {len(all_pos_dates)} 天")
print("-" * 80)
for d in all_pos_dates:
    day_stores = [r for r in results if any(x['date'] == d for x in r['pos'])]
    day_cnt = sum(x['count'] for r in results for x in r['pos'] if x['date'] == d)
    day_amt = sum(x['amount'] for r in results for x in r['pos'] if x['date'] == d)
    print(f"  {d}  {len(day_stores):>2}家门店  {day_cnt:>4}笔  ¥{day_amt:>12,.2f}")

print(f"\n四、外卖平台 Grab 有数据的日期: {len(all_to_dates)} 天")
print("-" * 80)
for d in all_to_dates:
    day_stores = [r for r in results if any(x['date'] == d for x in r['takeout'])]
    day_cnt = sum(x['count'] for r in results for x in r['takeout'] if x['date'] == d)
    day_amt = sum(x['amount'] for r in results for x in r['takeout'] if x['date'] == d)
    print(f"  {d}  {len(day_stores):>2}家门店  {day_cnt:>5}笔  ¥{day_amt:>12,.2f}")

# 5. 总计
total_pos_cnt = sum(d['count'] for r in results for d in r['pos'])
total_pos_amt = sum(d['amount'] for r in results for d in r['pos'])
total_to_cnt = sum(d['count'] for r in results for d in r['takeout'])
total_to_amt = sum(d['amount'] for r in results for d in r['takeout'])

print(f"\n" + "=" * 80)
print("五、总计")
print("=" * 80)
print(f"  POS侧 Grab:     {total_pos_cnt:>6}笔  ¥{total_pos_amt:>14,.2f}")
print(f"  外卖平台 Grab:  {total_to_cnt:>6}笔  ¥{total_to_amt:>14,.2f}")
print(f"  合计:           {total_pos_cnt + total_to_cnt:>6}笔  ¥{total_pos_amt + total_to_amt:>14,.2f}")
