#!/usr/bin/env python3
"""查询华莱士门店4月份Grab支付方式使用情况"""
import sys
sys.path.insert(0, '/home/weifashi/hwt/analysis')

from bq_reports.utils.bq_client import get_bq_client, STORE_UUIDS

client = get_bq_client()
BQ_LOCATION = "asia-southeast1"

# ========== 1. 先查询所有门店名称 ==========
print("=" * 70)
print("华莱士门店列表（从BigQuery获取）")
print("=" * 70)

stores = []
for store_uuid in STORE_UUIDS:
    dataset = f"shop{store_uuid}"
    try:
        job = client.query(
            f"""
            SELECT
              c.uuid AS store_uuid,
              c.name AS store_name,
              IFNULL(
                (SELECT JSON_EXTRACT_SCALAR(`values`, '$.store_code')
                 FROM `diyl-407103`.`{dataset}`.`ttpos_setting`
                 WHERE `key` = 'store' AND delete_time = 0 LIMIT 1), '') AS store_code
            FROM `diyl-407103`.`{dataset}`.`ttpos_company` c
            WHERE c.delete_time = 0
            LIMIT 1
            """,
            location=BQ_LOCATION
        )
        rows = list(job.result())
        if rows:
            stores.append({
                'uuid': store_uuid,
                'name': rows[0].store_name,
                'code': rows[0].store_code,
                'dataset': dataset
            })
    except Exception as e:
        print(f"  [跳过] {store_uuid}: {e}")

print(f"\n共 {len(stores)} 家门店:\n")
for i, s in enumerate(stores, 1):
    print(f"  {i:2}. {s['name']} (code={s['code']})")

# ========== 2. 查询4月份Grab支付方式数据 ==========
# 4月份时间范围（泰国时间 BKK +07:00）
# 2026-04-01 00:00:00 BKK = 2026-03-31 17:00:00 UTC = 1774976400
# 2026-05-01 00:00:00 BKK = 2026-04-30 17:00:00 UTC = 1777568400
START_TS = 1774976400  # 2026-04-01 00:00 BKK
END_TS = 1777568400    # 2026-05-01 00:00 BKK

print("\n" + "=" * 70)
print("4月份 Grab 支付方式使用情况")
print("=" * 70)

results = []

for s in stores:
    dataset = s['dataset']

    # --- 2a. POS侧 Grab 支付 ---
    pos_grab_sql = f"""
    SELECT
      DATE(TIMESTAMP_SECONDS(sp.complete_time), 'Asia/Bangkok') AS pay_date,
      pm.payment_name AS method_name,
      COUNT(*) AS bill_cnt,
      ROUND(SUM(sp.payment_amount), 2) AS total_amount
    FROM `diyl-407103`.`{dataset}`.`ttpos_statistics_payment` sp
    LEFT JOIN `diyl-407103`.`{dataset}`.`ttpos_payment_method` pm
      ON pm.uuid = sp.payment_method_uuid
    WHERE sp.delete_time = 0
      AND sp.complete_time >= {START_TS}
      AND sp.complete_time < {END_TS}
      AND LOWER(IFNULL(pm.payment_name, '')) LIKE '%grab%'
    GROUP BY pay_date, method_name
    ORDER BY pay_date
    """

    # --- 2b. 外卖平台 Grab ---
    takeout_grab_sql = f"""
    SELECT
      DATE(TIMESTAMP_SECONDS(
        CASE WHEN order_state = 40 AND completed_time > 0 THEN completed_time
             ELSE accepted_time END
      ), 'Asia/Bangkok') AS pay_date,
      'Grab(外卖平台)' AS method_name,
      COUNT(*) AS bill_cnt,
      ROUND(SUM(platform_total), 2) AS total_amount
    FROM `diyl-407103`.`{dataset}`.`ttpos_takeout_order`
    WHERE delete_time = 0
      AND order_state IN (10, 20, 30, 40)
      AND platform = 'grab'
      AND accepted_time > 0
      AND (CASE WHEN order_state = 40 AND completed_time > 0 THEN completed_time
                ELSE accepted_time END) >= {START_TS}
      AND (CASE WHEN order_state = 40 AND completed_time > 0 THEN completed_time
                ELSE accepted_time END) < {END_TS}
    GROUP BY pay_date
    ORDER BY pay_date
    """

    pos_dates = []
    takeout_dates = []

    try:
        job = client.query(pos_grab_sql, location=BQ_LOCATION)
        for row in job.result():
            pos_dates.append({
                'date': row.pay_date.isoformat() if row.pay_date else None,
                'method': row.method_name,
                'count': row.bill_cnt,
                'amount': float(row.total_amount) if row.total_amount else 0
            })
    except Exception as e:
        pass

    try:
        job = client.query(takeout_grab_sql, location=BQ_LOCATION)
        for row in job.result():
            takeout_dates.append({
                'date': row.pay_date.isoformat() if row.pay_date else None,
                'method': row.method_name,
                'count': row.bill_cnt,
                'amount': float(row.total_amount) if row.total_amount else 0
            })
    except Exception as e:
        pass

    if pos_dates or takeout_dates:
        results.append({
            'store': s,
            'pos_dates': pos_dates,
            'takeout_dates': takeout_dates
        })

# ========== 3. 输出结果 ==========
print(f"\n共有 {len(results)} 家门店在4月份有 Grab 相关数据:\n")

for r in results:
    s = r['store']
    print(f"\n{'─' * 70}")
    print(f"门店: {s['name']}")
    print(f"{'─' * 70}")

    if r['pos_dates']:
        print(f"  [POS侧 Grab 支付]")
        total_pos = sum(d['amount'] for d in r['pos_dates'])
        total_pos_cnt = sum(d['count'] for d in r['pos_dates'])
        for d in r['pos_dates']:
            print(f"    {d['date']} | {d['method']} | {d['count']}笔 | ¥{d['amount']:.2f}")
        print(f"    POS小计: {total_pos_cnt}笔 | ¥{total_pos:.2f}")

    if r['takeout_dates']:
        print(f"  [外卖平台 Grab]")
        total_to = sum(d['amount'] for d in r['takeout_dates'])
        total_to_cnt = sum(d['count'] for d in r['takeout_dates'])
        for d in r['takeout_dates']:
            print(f"    {d['date']} | {d['method']} | {d['count']}笔 | ¥{d['amount']:.2f}")
        print(f"    外卖小计: {total_to_cnt}笔 | ¥{total_to:.2f}")

# 汇总
print("\n" + "=" * 70)
print("汇总")
print("=" * 70)

all_dates_pos = set()
all_dates_takeout = set()
for r in results:
    for d in r['pos_dates']:
        all_dates_pos.add(d['date'])
    for d in r['takeout_dates']:
        all_dates_takeout.add(d['date'])

print(f"\n有POS侧Grab支付的门店数: {sum(1 for r in results if r['pos_dates'])}")
print(f"有外卖平台Grab的门店数: {sum(1 for r in results if r['takeout_dates'])}")

if all_dates_pos:
    print(f"\nPOS侧 Grab 支付产生数据的日期 ({len(all_dates_pos)}天):")
    for d in sorted(all_dates_pos):
        print(f"  {d}")

if all_dates_takeout:
    print(f"\n外卖平台 Grab 产生数据的日期 ({len(all_dates_takeout)}天):")
    for d in sorted(all_dates_takeout):
        print(f"  {d}")

# 没有Grab数据的门店
no_grab_stores = [s for s in stores if s['name'] not in [r['store']['name'] for r in results]]
print(f"\n没有Grab相关数据的门店 ({len(no_grab_stores)}家):")
for s in no_grab_stores:
    print(f"  - {s['name']}")
