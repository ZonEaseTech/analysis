#!/usr/bin/env python3
# 谁问的: 何伟涛 / 2026-05-28
# 问什么: 复现支付渠道对账表「系统」行(2026-04全门店),定位每渠道数据源
# 结论: 混合源 — QR/EDC/ALIPAY/CASH/RBH 来自 POS ttpos_statistics_payment(payment_name 分组),GRAB/LINEMAN/SHOPEE 来自外卖 ttpos_takeout_order(platform 分组 platform_total);POS QR 笼统口径吸收了刷卡/支付宝,需人工拆分
"""遍历所有 TH 门店,POS 按 payment_name 全量分组 + 外卖按 platform 全量分组,定位「系统」行各渠道数据源。"""
import sys
sys.path.insert(0, '/home/weifashi/hwt/analysis')

from collections import defaultdict
from bq_reports.utils.bq_client import get_bq_client

client = get_bq_client()
BQ_LOCATION = "asia-southeast1"

START_TS = 1774976400
END_TS = 1777568400

# 客户给的「系统」行 (2026-04 全门店合计)
CLIENT = {
    'QR': 10091913,
    'EDC': 345480,
    'ALIPAY': 13872,
    'GRAB': 4088499,
    'LINEMAN': 8185896,
    'SHOPEE': 4916184,
    'RBH': 6853.7,
    'CASH': 8328934,
}
CLIENT_TOTAL = 35977631.7

# ========== 1. 门店枚举(照搬参考脚本) ==========
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

datasets = list(client.list_datasets())
shop_dataset_ids = set(d.dataset_id for d in datasets if d.dataset_id.startswith('shop'))
stores = [s for s in all_th_stores if s['dataset'] in shop_dataset_ids]

print(f"TH 门店总数(配置): {len(all_th_stores)}, 实际存在 shop* dataset: {len(stores)}")

# ========== 2. 跨门店累加 ==========
# A) POS payment_name -> {amount, count}
pos_agg = defaultdict(lambda: {'amount': 0.0, 'count': 0})
# B) takeout platform -> {amount, count}
to_agg = defaultdict(lambda: {'amount': 0.0, 'count': 0})

pos_ok = pos_fail = to_ok = to_fail = 0

for s in stores:
    dataset = s['dataset']

    # --- POS 支付按 payment_name 全量分组 ---
    try:
        job = client.query(f"""
            SELECT IFNULL(pm.payment_name, '(NULL)') AS method_name,
                COUNT(*) AS bill_cnt,
                SUM(sp.payment_amount) AS total_amount
            FROM `diyl-407103`.`{dataset}`.`ttpos_statistics_payment` sp
            LEFT JOIN `diyl-407103`.`{dataset}`.`ttpos_payment_method` pm
                ON pm.uuid = sp.payment_method_uuid
            WHERE sp.delete_time = 0
                AND sp.complete_time >= {START_TS}
                AND sp.complete_time < {END_TS}
            GROUP BY method_name
        """, location=BQ_LOCATION)
        for row in job.result():
            name = row.method_name
            pos_agg[name]['amount'] += float(row.total_amount) if row.total_amount else 0.0
            pos_agg[name]['count'] += int(row.bill_cnt) if row.bill_cnt else 0
        pos_ok += 1
    except Exception:
        pos_fail += 1

    # --- 外卖按 platform 全量分组 ---
    try:
        job = client.query(f"""
            SELECT IFNULL(platform, '(NULL)') AS platform,
                COUNT(*) AS bill_cnt,
                SUM(platform_total) AS total_amount
            FROM `diyl-407103`.`{dataset}`.`ttpos_takeout_order`
            WHERE delete_time = 0
                AND order_state IN (10, 20, 30, 40)
                AND accepted_time > 0
                AND (CASE WHEN order_state = 40 AND completed_time > 0 THEN completed_time
                    ELSE accepted_time END) >= {START_TS}
                AND (CASE WHEN order_state = 40 AND completed_time > 0 THEN completed_time
                    ELSE accepted_time END) < {END_TS}
            GROUP BY platform
        """, location=BQ_LOCATION)
        for row in job.result():
            p = row.platform
            to_agg[p]['amount'] += float(row.total_amount) if row.total_amount else 0.0
            to_agg[p]['count'] += int(row.bill_cnt) if row.bill_cnt else 0
        to_ok += 1
    except Exception:
        to_fail += 1

print(f"POS 查询成功 {pos_ok} 店, 失败/缺表 {pos_fail} 店")
print(f"外卖查询成功 {to_ok} 店, 失败/缺表 {to_fail} 店")

# ========== 3. 输出 ==========
def fmt(x):
    return f"{x:,.2f}"

print("\n" + "=" * 78)
print("A) POS ttpos_statistics_payment — payment_name 全门店合计 (按金额降序)")
print("=" * 78)
print(f"{'payment_name':<32}{'金额':>20}{'笔数':>12}")
print("-" * 78)
pos_sorted = sorted(pos_agg.items(), key=lambda kv: kv[1]['amount'], reverse=True)
pos_total_amt = 0.0
pos_total_cnt = 0
for name, v in pos_sorted:
    print(f"{name:<32}{fmt(v['amount']):>20}{v['count']:>12,}")
    pos_total_amt += v['amount']
    pos_total_cnt += v['count']
print("-" * 78)
print(f"{'POS 合计':<32}{fmt(pos_total_amt):>20}{pos_total_cnt:>12,}")

print("\n" + "=" * 78)
print("B) 外卖 ttpos_takeout_order — platform 全门店合计 (按金额降序)")
print("=" * 78)
print(f"{'platform':<32}{'金额(platform_total)':>20}{'笔数':>12}")
print("-" * 78)
to_sorted = sorted(to_agg.items(), key=lambda kv: kv[1]['amount'], reverse=True)
to_total_amt = 0.0
to_total_cnt = 0
for p, v in to_sorted:
    print(f"{p:<32}{fmt(v['amount']):>20}{v['count']:>12,}")
    to_total_amt += v['amount']
    to_total_cnt += v['count']
print("-" * 78)
print(f"{'外卖合计':<32}{fmt(to_total_amt):>20}{to_total_cnt:>12,}")

# ========== 4. 对照分析 ==========
print("\n" + "=" * 78)
print("C) 对照分析 — 客户「系统」行各渠道 vs 真实数据源")
print("=" * 78)

# 关键字匹配规则 (大小写不敏感), 只用于"建议候选", 不硬合并
POS_KEYWORDS = {
    'QR':     ['qr', 'prompt', 'thai qr', 'scan'],
    'EDC':    ['edc', 'card', 'credit', 'visa', 'master', '刷卡', 'pos机'],
    'ALIPAY': ['alipay', '支付宝', 'ali'],
    'CASH':   ['cash', '现金', 'เงินสด'],
    'RBH':    ['rbh', 'robinhood', 'robin'],
    'GRAB':   ['grab'],
    'LINEMAN':['lineman', 'line man', 'line'],
    'SHOPEE': ['shopee'],
}
TO_KEYWORDS = {
    'GRAB':   ['grab'],
    'LINEMAN':['lineman', 'line'],
    'SHOPEE': ['shopee'],
}

def find_pos_candidates(keys):
    out = []
    for name, v in pos_sorted:
        lname = name.lower()
        if any(k in lname for k in keys):
            out.append((name, v['amount'], v['count']))
    return out

def find_to_candidates(keys):
    out = []
    for p, v in to_sorted:
        lp = p.lower()
        if any(k in lp for k in keys):
            out.append((p, v['amount'], v['count']))
    return out

for ch in ['QR', 'EDC', 'ALIPAY', 'GRAB', 'LINEMAN', 'SHOPEE', 'RBH', 'CASH']:
    client_amt = CLIENT[ch]
    print(f"\n--- 渠道 [{ch}]  客户值 = {fmt(client_amt)} ---")

    pos_cands = find_pos_candidates(POS_KEYWORDS.get(ch, []))
    to_cands = find_to_candidates(TO_KEYWORDS.get(ch, [])) if ch in TO_KEYWORDS else []

    if pos_cands:
        print("  [POS payment_name 候选]")
        for name, amt, cnt in pos_cands:
            print(f"     payment_name={name!r:<28} 金额={fmt(amt):>16} 笔数={cnt:>8,} 差={fmt(amt-client_amt)}")
        psum = sum(a for _, a, _ in pos_cands)
        if len(pos_cands) > 1:
            print(f"     >>> POS 候选合计 = {fmt(psum)}  vs 客户 = {fmt(client_amt)}  差 = {fmt(psum-client_amt)}")
    else:
        print("  [POS payment_name 候选] 无匹配关键字")

    if ch in TO_KEYWORDS:
        if to_cands:
            print("  [外卖 platform 候选]")
            for p, amt, cnt in to_cands:
                print(f"     platform={p!r:<28} 金额={fmt(amt):>16} 笔数={cnt:>8,} 差={fmt(amt-client_amt)}")
        else:
            print("  [外卖 platform 候选] 无匹配")

print("\n" + "=" * 78)
print("D) 总额对照")
print("=" * 78)
print(f"客户「系统」合计           = {fmt(CLIENT_TOTAL)}")
print(f"POS 全渠道合计             = {fmt(pos_total_amt)}")
print(f"外卖全平台合计             = {fmt(to_total_amt)}")
print(f"POS + 外卖 朴素合计        = {fmt(pos_total_amt + to_total_amt)}")
print("(注: 朴素合计会双算外卖在 POS 里也记的部分, 仅供参考)")
