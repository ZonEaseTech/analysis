"""对账: csv 里的 215 单是不是图里 13 行 (店,日期,渠道) 桶的完整覆盖。
若不一致, 那图里 13 行的真实金额可能仍未完全扣除, 差额会冒出来。"""
import sys, csv
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID, STORE_UUIDS

setup_proxy(); c = get_bq_client()
BKK = timezone(timedelta(hours=7))

# 图里 13 行 (店, 日期, 渠道)
YELLOW = [
    ('005', '2026-02-10', 'takeout', 12, 1911),
    ('006', '2026-02-10', 'dine',    11, 1869),
    ('011', '2026-02-01', 'dine',    55, 8528),
    ('035', '2026-02-11', 'dine',     9, 1397),
    ('035', '2026-02-12', 'dine',     1,  348),
    ('035', '2026-02-13', 'dine',     5,  883),
    ('051', '2026-02-16', 'dine',     2,  416),
    ('051', '2026-02-17', 'dine',     1,  159),
    ('051', '2026-02-18', 'dine',     3,  347),
    ('051', '2026-02-19', 'dine',     1,  168),
    ('051', '2026-02-20', 'dine',     1,  268),
    ('051', '2026-02-21', 'dine',     1,  159),
    ('051', '2026-02-23', 'dine',     5,  883),
]

# 加载 csv: 把每个 uuid 的 (store, channel) 都拿到
with open('resources/wallace.20260515/order_excludes.csv', encoding='utf-8-sig') as f:
    csv_rows = list(csv.DictReader(f))
dine_uuids = set(int(r['exclude_key']) for r in csv_rows if r['channel']=='dine')
take_uuids = set(int(r['exclude_key']) for r in csv_rows if r['channel']=='takeout')
print(f"csv: dine {len(dine_uuids)} 单, takeout {len(take_uuids)} 单")

# 加载 merchants 找 store_num -> store_uuid
import pandas as pd
mer = pd.read_excel('resources/wallace.20260506/华莱士商家56家ID.xlsx', dtype=str)
print("\n商家表前几列:", mer.columns.tolist()[:6])
# 找 store_num -> uuid 映射
store_to_uuid = {}
for _, row in mer.iterrows():
    sn = str(row.iloc[2]).strip() if len(row) > 2 else None  # 第3列大概率是 store_num
    uuid = str(row.iloc[1]).strip() if len(row) > 1 else None
    if sn and uuid and uuid != 'nan':
        # 试不同候选, store_num 是 3 位数 '005' 这种
        if sn.isdigit() and len(sn) <= 3:
            store_to_uuid[sn.zfill(3)] = uuid

# 直接写死 (前面已反查) — 这 7 店是 csv 涉及的店, 够了
store_to_uuid = {
    '005': '3446618988544000',
    '006': '3870122057728000',
    '011': '5999171739648000',
    '035': '8051063001088000',
    '051': '2618629820416000',
    '053': '2992442970112000',
    '061': '3087884357632000',
}

print(f"取到 {len(store_to_uuid)} 个 store_num→uuid")
needed = {'005','006','011','035','051'}
for sn in needed:
    print(f"  {sn} → {store_to_uuid.get(sn, '<未找到>')}")

# 对每个标黄桶, 反查 BQ 该店该日 dine/takeout 真实支付单
print("\n=== 对账: 每个标黄桶, csv 里覆盖几单 / BQ 里真实几单 ===")
print(f"{'店':>4} {'日期':<12} {'渠道':<8} {'图笔数':>5} {'图金额':>8} {'BQ单数':>6} {'BQ实收':>10} {'csv命中':>7} {'csv金额':>9}")
total_yellow = 0
total_bq = 0
total_csv = 0
for sn, date, ch, n_yellow, amt_yellow in YELLOW:
    u = store_to_uuid.get(sn)
    if not u:
        print(f"  {sn} 找不到 uuid")
        continue
    day_start = int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=BKK).timestamp())
    day_end = day_start + 86400
    if ch == 'dine':
        # BQ 该店该日所有 sale_bill (status=1, 不 hide, 不 delete) 的 sale_order_uuid 集合
        sql = f"""
SELECT DISTINCT sp.sale_order_uuid, sp.sale_bill_uuid,
  IF(sp.free_num>0 OR sp.give_num>0, 0,
     sp.product_final_price*(sp.product_num - sp.refund_num)) AS actual
FROM `{PROJECT_ID}.shop{u}.ttpos_statistics_product` sp
JOIN `{PROJECT_ID}.shop{u}.ttpos_sale_bill` sb ON sb.uuid = sp.sale_bill_uuid
WHERE sp.complete_time >= {day_start} AND sp.complete_time < {day_end}
  AND sb.delete_time = 0 AND sb.status = 1
"""
        rows = list(c.query(sql).result())
        # 按 sale_bill_uuid 折叠为账单数 (图里"笔数"是账单数)
        bills = set()
        bill_amounts = defaultdict(float)
        for r in rows:
            bills.add(r['sale_bill_uuid'])
            bill_amounts[r['sale_bill_uuid']] += float(r['actual'] or 0)
        bq_count = len(bills)
        bq_actual = sum(bill_amounts.values())
        csv_hit_count = sum(1 for r in rows if int(r['sale_order_uuid']) in dine_uuids)
        csv_hit_amount = sum(float(r['actual'] or 0) for r in rows if int(r['sale_order_uuid']) in dine_uuids)
    else:  # takeout
        sql = f"""
SELECT t.uuid AS order_uuid,
  SUM(IF(t.order_state IN (10,20,30,40), toi.price*toi.quantity, 0)) AS actual
FROM `{PROJECT_ID}.shop{u}.ttpos_takeout_order_item` toi
JOIN `{PROJECT_ID}.shop{u}.ttpos_takeout_order` t ON t.uuid=toi.takeout_order_uuid AND t.delete_time=0
WHERE toi.delete_time=0 AND toi.ttpos_product_package_uuid>0
  AND t.order_state IN (10,20,30,40,60) AND t.accepted_time>0
  AND ((t.order_state=40 AND t.completed_time>={day_start} AND t.completed_time<{day_end})
       OR (t.order_state!=40 AND t.accepted_time>={day_start} AND t.accepted_time<{day_end}))
GROUP BY t.uuid
"""
        rows = list(c.query(sql).result())
        bq_count = len(rows)
        bq_actual = sum(float(r['actual'] or 0) for r in rows)
        csv_hit_count = sum(1 for r in rows if int(r['order_uuid']) in take_uuids)
        csv_hit_amount = sum(float(r['actual'] or 0) for r in rows if int(r['order_uuid']) in take_uuids)
    total_yellow += amt_yellow
    total_bq += bq_actual
    total_csv += csv_hit_amount
    mark = "" if abs(csv_hit_amount - amt_yellow) < 1 else "  ⚠️"
    print(f"  {sn:>3} {date:<12} {ch:<8} {n_yellow:>5} {amt_yellow:>8,} {bq_count:>6} {bq_actual:>10,.0f} {csv_hit_count:>7} {csv_hit_amount:>9,.0f}{mark}")
print(f"\n  合计:                            图金额={total_yellow:,}  BQ实收={total_bq:,.0f}  csv命中={total_csv:,.0f}")
print(f"  差 (图 - csv命中) = {total_yellow - total_csv:,.0f}")
print(f"  差 (BQ - csv命中) = {total_bq - total_csv:,.0f}  &lt;-- 这才是真漏过滤金额")
