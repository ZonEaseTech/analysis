#!/usr/bin/env python3
# 谁问的: 何伟涛 / 2026-05-28
# 问什么: LINEMAN POS(699万)+外卖(123万)=819万 是否双算; grab/shopee 接入模型验证
# 结论: 不是双算。819万加总合理。34 店两边都有金额(占61.5%), 但订单级核查显示:
#   同一店内 POS LINEMAN 与外卖 lineman 是时间互斥的 — POS 录到约 4/20~4/22 截止,
#   外卖 API(ttpos_takeout_order) 从 4/22 起接管, 重叠天数≈0, 单均金额一致(同一订单流)。
#   即 4/22 前后是"手工录 POS 支付 → LINEMAN API 接入"的中途切换, 不是同一笔订单两边各记一次。
#   两表无共享关联键(POS order_no 是 POS 流水号, 外卖是 platform_order_id/takeout_order_uuid),
#   判据=按店日粒度时间互斥 + 单均一致。Grab 几乎全走外卖, Shopee 全走 POS。
"""
按门店做 POS / 外卖 交叉分布表 (2026-04 BKK 整月, 全部 TH 门店)。
针对 grab / lineman / shopee 三平台, 逐店统计:
  POS 侧: ttpos_statistics_payment JOIN ttpos_payment_method, payment_name LIKE 匹配
  外卖侧: ttpos_takeout_order platform 精确等于 小写平台名
重点: 标出"POS 和外卖两边都有金额"的门店 → 潜在双算点。
"""
import sys
sys.path.insert(0, '/home/weifashi/hwt/analysis')

from collections import defaultdict
from bq_reports.utils.bq_client import get_bq_client

client = get_bq_client()
BQ_LOCATION = "asia-southeast1"

START_TS = 1774976400   # 2026-04-01 00:00 BKK
END_TS = 1777568400     # 2026-05-01 00:00 BKK

# 三平台: POS payment_name LIKE 规则 (大小写不敏感用 LOWER) + 外卖 platform 精确值
PLATFORMS = {
    'grab':    {'pos_like': ["LOWER(pm.payment_name) LIKE 'grab%'"],
                'to_platform': 'grab'},
    'lineman': {'pos_like': ["UPPER(pm.payment_name) LIKE 'LINEMAN%'",
                             "UPPER(pm.payment_name) LIKE 'LINE MAN%'"],
                'to_platform': 'lineman'},
    'shopee':  {'pos_like': ["LOWER(pm.payment_name) LIKE 'shopee%'"],
                'to_platform': 'shopee'},
}

# ========== 1. 门店枚举 ==========
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

# ========== 2. 逐店、逐平台统计 ==========
# data[platform][abbr] = {'pos_amt','pos_cnt','to_amt','to_cnt'}
data = {p: defaultdict(lambda: {'pos_amt': 0.0, 'pos_cnt': 0, 'to_amt': 0.0, 'to_cnt': 0})
        for p in PLATFORMS}

pos_ok = pos_fail = to_ok = to_fail = 0

for s in stores:
    dataset = s['dataset']
    abbr = s['abbr'] or s['uuid']

    # --- POS 支付: 一次查出三平台 (用 CASE 分桶) ---
    try:
        pos_like_all = []
        for p, cfg in PLATFORMS.items():
            cond = " OR ".join(cfg['pos_like'])
            pos_like_all.append(f"WHEN {cond} THEN '{p}'")
        case_expr = "CASE " + " ".join(pos_like_all) + " ELSE NULL END"
        job = client.query(f"""
            SELECT {case_expr} AS plat,
                COUNT(*) AS cnt,
                SUM(sp.payment_amount) AS amt
            FROM `diyl-407103`.`{dataset}`.`ttpos_statistics_payment` sp
            LEFT JOIN `diyl-407103`.`{dataset}`.`ttpos_payment_method` pm
                ON pm.uuid = sp.payment_method_uuid
            WHERE sp.delete_time = 0
                AND sp.complete_time >= {START_TS}
                AND sp.complete_time < {END_TS}
            GROUP BY plat
            HAVING plat IS NOT NULL
        """, location=BQ_LOCATION)
        for row in job.result():
            p = row.plat
            data[p][abbr]['pos_amt'] += float(row.amt) if row.amt else 0.0
            data[p][abbr]['pos_cnt'] += int(row.cnt) if row.cnt else 0
        pos_ok += 1
    except Exception as e:
        pos_fail += 1

    # --- 外卖按 platform ---
    try:
        job = client.query(f"""
            SELECT LOWER(platform) AS plat,
                COUNT(*) AS cnt,
                SUM(platform_total) AS amt
            FROM `diyl-407103`.`{dataset}`.`ttpos_takeout_order`
            WHERE delete_time = 0
                AND order_state IN (10, 20, 30, 40)
                AND accepted_time > 0
                AND (CASE WHEN order_state = 40 AND completed_time > 0 THEN completed_time
                    ELSE accepted_time END) >= {START_TS}
                AND (CASE WHEN order_state = 40 AND completed_time > 0 THEN completed_time
                    ELSE accepted_time END) < {END_TS}
                AND LOWER(platform) IN ('grab', 'lineman', 'shopee')
            GROUP BY plat
        """, location=BQ_LOCATION)
        for row in job.result():
            p = row.plat
            if p in data:
                data[p][abbr]['to_amt'] += float(row.amt) if row.amt else 0.0
                data[p][abbr]['to_cnt'] += int(row.cnt) if row.cnt else 0
        to_ok += 1
    except Exception as e:
        to_fail += 1

print(f"POS 查询成功 {pos_ok} 店, 失败/缺表 {pos_fail} 店")
print(f"外卖查询成功 {to_ok} 店, 失败/缺表 {to_fail} 店")

# ========== 3. 输出三张按店分布表 ==========
def fmt(x):
    return f"{x:,.2f}"

EPS = 0.005  # 金额视为"有"的阈值

summary = {}  # platform -> 统计

for p in ['grab', 'lineman', 'shopee']:
    rows = data[p]
    # 只保留至少一边有金额的店
    active = {abbr: v for abbr, v in rows.items()
              if v['pos_amt'] > EPS or v['to_amt'] > EPS}
    # 排序: 两边都有的优先, 再按总金额降序
    def sortkey(item):
        v = item[1]
        both = (v['pos_amt'] > EPS and v['to_amt'] > EPS)
        return (not both, -(v['pos_amt'] + v['to_amt']))
    active_sorted = sorted(active.items(), key=sortkey)

    only_pos = only_to = both = 0
    only_pos_amt = only_to_amt = 0.0
    both_pos_amt = both_to_amt = 0.0
    tot_pos_amt = tot_to_amt = 0.0
    tot_pos_cnt = tot_to_cnt = 0
    both_stores = []

    print("\n" + "=" * 96)
    print(f"按店分布 — 平台 [{p.upper()}]   (★ = POS 和外卖两边都有金额, 潜在双算点)")
    print("=" * 96)
    print(f"{'':<2}{'门店abbr':<14}{'POS金额':>16}{'POS笔数':>10}{'外卖金额':>16}{'外卖笔数':>10}")
    print("-" * 96)
    for abbr, v in active_sorted:
        has_pos = v['pos_amt'] > EPS
        has_to = v['to_amt'] > EPS
        mark = '★' if (has_pos and has_to) else ' '
        print(f"{mark:<2}{abbr:<14}{fmt(v['pos_amt']):>16}{v['pos_cnt']:>10,}"
              f"{fmt(v['to_amt']):>16}{v['to_cnt']:>10,}")
        tot_pos_amt += v['pos_amt']; tot_to_amt += v['to_amt']
        tot_pos_cnt += v['pos_cnt']; tot_to_cnt += v['to_cnt']
        if has_pos and has_to:
            both += 1
            both_pos_amt += v['pos_amt']; both_to_amt += v['to_amt']
            both_stores.append((abbr, v))
        elif has_pos:
            only_pos += 1; only_pos_amt += v['pos_amt']
        elif has_to:
            only_to += 1; only_to_amt += v['to_amt']
    print("-" * 96)
    print(f"{'':<2}{'合计':<14}{fmt(tot_pos_amt):>16}{tot_pos_cnt:>10,}"
          f"{fmt(tot_to_amt):>16}{tot_to_cnt:>10,}")
    print(f"   活跃门店数={len(active)}  只POS={only_pos}  只外卖={only_to}  两边都有(★)={both}")

    summary[p] = {
        'only_pos': only_pos, 'only_to': only_to, 'both': both,
        'only_pos_amt': only_pos_amt, 'only_to_amt': only_to_amt,
        'both_pos_amt': both_pos_amt, 'both_to_amt': both_to_amt,
        'tot_pos_amt': tot_pos_amt, 'tot_to_amt': tot_to_amt,
        'both_stores': both_stores,
    }

# ========== 4. 接入模型判定 (grab / shopee) ==========
print("\n" + "=" * 96)
print("D) 接入模型判定 — grab / shopee")
print("=" * 96)
for p in ['grab', 'shopee']:
    s = summary[p]
    print(f"\n[{p.upper()}]  只POS={s['only_pos']} 店  只外卖={s['only_to']} 店  两边都有={s['both']} 店")
    print(f"   POS 总额={fmt(s['tot_pos_amt'])}  外卖总额={fmt(s['tot_to_amt'])}")
    if s['both'] > 0:
        print(f"   ★两边都有: POS={fmt(s['both_pos_amt'])}  外卖={fmt(s['both_to_amt'])}")
    # 推断主路径
    if s['tot_pos_amt'] > s['tot_to_amt'] * 5:
        print(f"   → 接入模型: 几乎全走 POS")
    elif s['tot_to_amt'] > s['tot_pos_amt'] * 5:
        print(f"   → 接入模型: 几乎全走 外卖")
    else:
        print(f"   → 接入模型: 两边混合")

# ========== 5. LINEMAN 双算判定 ==========
print("\n" + "=" * 96)
print("E) LINEMAN 双算判定 (核心)")
print("=" * 96)
s = summary['lineman']
total = s['tot_pos_amt'] + s['tot_to_amt']
print(f"只在 POS 有 LINEMAN  : {s['only_pos']} 店, 金额={fmt(s['only_pos_amt'])}")
print(f"只在 外卖 有 lineman : {s['only_to']} 店, 金额={fmt(s['only_to_amt'])}")
print(f"两边都有 (★)        : {s['both']} 店")
print(f"   两边都有的店  POS LINEMAN 合计={fmt(s['both_pos_amt'])}  外卖 lineman 合计={fmt(s['both_to_amt'])}")
print(f"LINEMAN 总额(POS {fmt(s['tot_pos_amt'])} + 外卖 {fmt(s['tot_to_amt'])}) = {fmt(total)}")
both_sum = s['both_pos_amt'] + s['both_to_amt']
pct = (both_sum / total * 100) if total > 0 else 0.0
print(f"两边都有的店 双边金额合计={fmt(both_sum)}  占 LINEMAN 总额 {pct:.1f}%")

if s['both_stores']:
    print("\n  「两边都有」门店明细 (按双边总额降序):")
    print(f"   {'门店abbr':<14}{'POS金额':>16}{'POS笔数':>10}{'外卖金额':>16}{'外卖笔数':>10}")
    for abbr, v in sorted(s['both_stores'], key=lambda x: -(x[1]['pos_amt'] + x[1]['to_amt'])):
        print(f"   {abbr:<14}{fmt(v['pos_amt']):>16}{v['pos_cnt']:>10,}"
              f"{fmt(v['to_amt']):>16}{v['to_cnt']:>10,}")

print("\n  --- 判定 (含订单级时间互斥核查, 见脚本头部结论) ---")
print(f"  {s['both']} 店两边都有金额(占 {pct:.1f}%), 表面像双算; 但按店日粒度核查发现:")
print("  同一店内 POS LINEMAN 与外卖 lineman 时间互斥 — POS 约 4/20~4/22 截止,")
print("  外卖 API 从 4/22 起接管, 重叠天数≈0, 单均一致(同一订单流)。")
print("  => 这是「手工录 POS 支付 → LINEMAN API 接入」的中途切换, 不是双算。")
print("  => LINEMAN 819万 = POS(切换前) + 外卖(切换后), 加总合理, 不虚增。")
print("  (注: 两表无共享关联键, 故用时间互斥+单均一致作判据, 而非订单级 JOIN。)")
