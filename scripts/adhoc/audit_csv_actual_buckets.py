"""把 csv 里 215 单 → BQ 查到的实际 (店, 日期, 实收金额) 分布列出来,
跟图里 13 桶对比, 找出 csv 误删和漏删的 (店, 日期) 桶。"""
import sys, csv as csvm
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID

setup_proxy(); c = get_bq_client()
BKK = timezone(timedelta(hours=7))

STORE = {
    '005': '3446618988544000',
    '006': '3870122057728000',
    '011': '5999171739648000',
    '035': '8051063001088000',
    '051': '2618629820416000',
    '053': '2992442970112000',
    '061': '3087884357632000',
}

with open('resources/wallace.20260515/order_excludes.csv', encoding='utf-8-sig') as f:
    rows = list(csvm.DictReader(f))

# 按 store_num 分组, 然后到 BQ 反查每单 complete_time / accepted_time → 落在哪一天
by_store = defaultdict(lambda: {'dine': [], 'takeout': []})
for r in rows:
    by_store[r['store_num']][r['channel']].append(int(r['exclude_key']))

# 图里 13 桶 (店, 日期, 渠道) → 应扣金额
YELLOW_BUCKETS = {
    ('005','2026-02-10','takeout'): 1911,
    ('006','2026-02-10','dine'): 1869,
    ('011','2026-02-01','dine'): 8528,
    ('035','2026-02-11','dine'): 1397,
    ('035','2026-02-12','dine'): 348,
    ('035','2026-02-13','dine'): 883,
    ('051','2026-02-16','dine'): 416,
    ('051','2026-02-17','dine'): 159,
    ('051','2026-02-18','dine'): 347,
    ('051','2026-02-19','dine'): 168,
    ('051','2026-02-20','dine'): 168,  # 笔记: 图里 268, 我打错了, 用 268
    ('051','2026-02-21','dine'): 159,
    ('051','2026-02-23','dine'): 883,
}
YELLOW_BUCKETS[('051','2026-02-20','dine')] = 268  # 修正

# 反查 csv 每单的 (store, date, channel, actual)
csv_distribution = defaultdict(float)  # (store, date, channel) -> amount
csv_count = defaultdict(int)

for sn, channels in by_store.items():
    u = STORE.get(sn)
    if not u: continue
    if channels['dine']:
        ids = ','.join(map(str, channels['dine']))
        sql = f"""
SELECT sp.sale_order_uuid AS uid,
  DATE(TIMESTAMP_SECONDS(sp.complete_time), 'Asia/Bangkok') AS d,
  SUM(IF(sp.free_num>0 OR sp.give_num>0, 0,
     sp.product_final_price*(sp.product_num - sp.refund_num))) AS amt
FROM `{PROJECT_ID}.shop{u}.ttpos_statistics_product` sp
WHERE sp.sale_order_uuid IN ({ids})
GROUP BY uid, d
"""
        for r in c.query(sql).result():
            d = str(r['d'])
            csv_distribution[(sn, d, 'dine')] += float(r['amt'] or 0)
            csv_count[(sn, d, 'dine')] += 1
    if channels['takeout']:
        ids = ','.join(map(str, channels['takeout']))
        sql = f"""
SELECT t.uuid AS uid,
  DATE(TIMESTAMP_SECONDS(t.completed_time), 'Asia/Bangkok') AS d,
  SUM(IF(t.order_state IN (10,20,30,40), toi.price*toi.quantity, 0)) AS amt
FROM `{PROJECT_ID}.shop{u}.ttpos_takeout_order_item` toi
JOIN `{PROJECT_ID}.shop{u}.ttpos_takeout_order` t ON t.uuid=toi.takeout_order_uuid AND t.delete_time=0
WHERE toi.delete_time=0 AND toi.ttpos_product_package_uuid>0
  AND t.uuid IN ({ids})
GROUP BY uid, d
"""
        for r in c.query(sql).result():
            d = str(r['d'])
            csv_distribution[(sn, d, 'takeout')] += float(r['amt'] or 0)
            csv_count[(sn, d, 'takeout')] += 1

# 列出 csv 实际覆盖的所有桶
print("=== csv 215 单实际落在的桶 (按金额降序) ===")
print(f"{'店':>4} {'日期':<12} {'渠道':<8} {'csv单数':>8} {'csv金额':>10} {'图里有？':>10}")
total_csv = 0
in_yellow = 0
out_yellow = 0
for k, amt in sorted(csv_distribution.items(), key=lambda x: -x[1]):
    sn, d, ch = k
    in_y = k in YELLOW_BUCKETS
    mark = "✅在图里" if in_y else "❌图里没有 (csv误删?)"
    print(f"  {sn:>3} {d:<12} {ch:<8} {csv_count[k]:>8} {amt:>10,.2f}  {mark}")
    total_csv += amt
    if in_y: in_yellow += amt
    else: out_yellow += amt

print(f"\ncsv 命中合计: {total_csv:,.2f}")
print(f"  其中: 落在图里 13 桶: {in_yellow:,.2f}")
print(f"  其中: 落在图外      : {out_yellow:,.2f}  ← csv 误删")

# 哪些图里桶 csv 漏了
print("\n=== 图里桶 vs csv 命中 ===")
missing_buckets = 0
for bk, expected in YELLOW_BUCKETS.items():
    actual = csv_distribution.get(bk, 0)
    sn, d, ch = bk
    if abs(actual - expected) > 0.5:
        diff = expected - actual
        missing_buckets += diff
        print(f"  {sn} {d} {ch:<8} 图={expected:>6,.0f}  csv={actual:>8,.2f}  漏={diff:>6,.2f}")
print(f"\n图里漏的合计: {missing_buckets:,.2f}")
print(f"csv 误删 (图外): {out_yellow:,.2f}")
