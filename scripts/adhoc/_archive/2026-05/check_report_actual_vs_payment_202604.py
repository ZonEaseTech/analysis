#!/usr/bin/env python3
# 谁问的: 何伟涛 / 2026-05-28
# 问什么: 报表商品轴实收(sale_event actual_amount) vs 支付轴/客户系统/客户账上 四口径对照, 排查报表实收 vs 银行到账差 ~2.7w
# 结论: ① 报表实收(商品轴)=36,095,719.30 (堂食30,792,821.30 + 外卖5,302,898.00), 反而比 ② 支付轴(35,989,495.70)高10.6w、比 ④ 客户账上(35,950,205.40)高14.5w —— 报表不是"少了2.7w"而是"多算"。外卖 platform_total(5,302,829)≈商品行 price×qty(5,302,898), 差仅-69, 说明华莱士 platform_total 不含配送/包装费, 外卖口径不是差异来源。差异主因在堂食轴: 商品行 final_price×净量 含整单服务费/税/小费/抹零未在支付轴对齐, 且报表口径无 delete_time 过滤(表无该列), 比支付轴(payment 净额, 已扣退/抹零)系统性偏高。
"""遍历所有 TH 门店, 严格复刻 semantic/entities/sale_event.py 的堂食/外卖 actual_amount 口径,
跨店累加得 ① 报表实收(商品轴), 并与 ② 支付轴系统真值 / ③ 客户系统行 / ④ 客户账上 并排对照。
同时算外卖 platform_total 以量化 toi.price×qty vs platform_total 的差额。"""
import sys
sys.path.insert(0, '/home/weifashi/hwt/analysis')

from bq_reports.utils.bq_client import get_bq_client

client = get_bq_client()
BQ_LOCATION = "asia-southeast1"

START_TS = 1774976400
END_TS = 1777568400

# 已知三口径 (用户给定)
PAY_AXIS_TRUTH = 35989495.70   # ② 支付轴系统真值
CLIENT_SYSTEM = 35977631.70    # ③ 客户「系统」行
CLIENT_BANK = 35950205.40      # ④ 客户「账上」实际到账


def fmt(x):
    return f"{x:,.2f}"


# ========== 1. 门店枚举 (照搬参考脚本) ==========
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
        'dataset': f"shop{row.uuid}",
    })

datasets = list(client.list_datasets())
shop_dataset_ids = set(d.dataset_id for d in datasets if d.dataset_id.startswith('shop'))
stores = [s for s in all_th_stores if s['dataset'] in shop_dataset_ids]

print(f"TH 门店总数(配置): {len(all_th_stores)}, 实际存在 shop* dataset: {len(stores)}")

# ========== 2. 跨门店累加 ==========
dine_actual_total = 0.0          # 堂食 actual (sale_event 堂食口径)
takeout_actual_total = 0.0       # 外卖 actual (sale_event 外卖口径, toi.price×qty)
takeout_platform_total = 0.0     # 同一批外卖订单的 platform_total (整单口径)

dine_ok = dine_fail = 0
to_ok = to_fail = 0

for s in stores:
    dataset = s['dataset']

    # --- 堂食 actual: 严格复刻 sale_event 堂食 actual_amount ---
    try:
        job = client.query(f"""
            SELECT
              SUM(IF(sp.free_num > 0 OR sp.give_num > 0, 0,
                     sp.product_final_price * (sp.product_num - sp.refund_num))) AS dine_actual
            FROM `diyl-407103`.`{dataset}`.`ttpos_statistics_product` sp
            WHERE sp.complete_time >= {START_TS}
              AND sp.complete_time < {END_TS}
        """, location=BQ_LOCATION)
        for row in job.result():
            dine_actual_total += float(row.dine_actual) if row.dine_actual else 0.0
        dine_ok += 1
    except Exception:
        dine_fail += 1

    # --- 外卖 actual: 严格复刻 sale_event 外卖口径 (商品行 price×qty) ---
    #     同时用同一过滤集统计 t.platform_total (整单口径, 去重: 一单一次)
    try:
        job = client.query(f"""
            SELECT
              SUM(IF(t.order_state IN (10,20,30,40), toi.price * toi.quantity, 0)) AS takeout_actual
            FROM `diyl-407103`.`{dataset}`.`ttpos_takeout_order_item` toi
            JOIN `diyl-407103`.`{dataset}`.`ttpos_takeout_order` t
              ON t.uuid = toi.takeout_order_uuid AND t.delete_time = 0
            WHERE toi.delete_time = 0
              AND toi.ttpos_product_package_uuid > 0
              AND t.order_state IN (10, 20, 30, 40, 60)
              AND t.accepted_time > 0
              AND (
                (t.order_state = 40 AND t.completed_time >= {START_TS} AND t.completed_time < {END_TS})
                OR (t.order_state != 40 AND t.accepted_time >= {START_TS} AND t.accepted_time < {END_TS})
              )
        """, location=BQ_LOCATION)
        for row in job.result():
            takeout_actual_total += float(row.takeout_actual) if row.takeout_actual else 0.0

        # platform_total: 整单口径, 仅 state 10/20/30/40 (与支付轴/客户口径一致),
        # 去重到订单级 (不 JOIN item), 时间窗按 state 选 completed/accepted, 同 sale_event 外卖窗口
        job = client.query(f"""
            SELECT SUM(t.platform_total) AS pt
            FROM `diyl-407103`.`{dataset}`.`ttpos_takeout_order` t
            WHERE t.delete_time = 0
              AND t.order_state IN (10, 20, 30, 40)
              AND t.accepted_time > 0
              AND (
                (t.order_state = 40 AND t.completed_time >= {START_TS} AND t.completed_time < {END_TS})
                OR (t.order_state != 40 AND t.accepted_time >= {START_TS} AND t.accepted_time < {END_TS})
              )
        """, location=BQ_LOCATION)
        for row in job.result():
            takeout_platform_total += float(row.pt) if row.pt else 0.0
        to_ok += 1
    except Exception:
        to_fail += 1

print(f"堂食查询成功 {dine_ok} 店, 失败/缺表 {dine_fail} 店")
print(f"外卖查询成功 {to_ok} 店, 失败/缺表 {to_fail} 店")

# ========== 3. 四口径对照 ==========
report_actual = dine_actual_total + takeout_actual_total  # ① 报表实收(商品轴)

print("\n" + "=" * 78)
print("① 报表实收(商品轴) 拆分")
print("=" * 78)
print(f"{'堂食 dine_actual':<32}{fmt(dine_actual_total):>22}")
print(f"{'外卖 takeout_actual (price×qty)':<32}{fmt(takeout_actual_total):>22}")
print("-" * 54)
print(f"{'① 报表实收合计':<32}{fmt(report_actual):>22}")

print("\n" + "=" * 78)
print("四口径并排对照 (2026-04 全 TH 门店)")
print("=" * 78)
rows = [
    ("① 报表实收(商品轴)", report_actual, "dine final_price×净量 + 外卖 price×qty"),
    ("② 支付轴系统真值", PAY_AXIS_TRUTH, "ttpos_statistics_payment 净额 + 外卖 platform_total"),
    ("③ 客户「系统」行", CLIENT_SYSTEM, "客户对账表系统行"),
    ("④ 客户「账上」", CLIENT_BANK, "客户外部对账实际到账"),
]
print(f"{'口径':<22}{'金额':>20}  含义")
print("-" * 78)
for label, amt, desc in rows:
    print(f"{label:<22}{fmt(amt):>20}  {desc}")

# ========== 4. 差异结构分析 ==========
print("\n" + "=" * 78)
print("差异结构分析")
print("=" * 78)

d12 = report_actual - PAY_AXIS_TRUTH
d14 = report_actual - CLIENT_BANK
d13 = report_actual - CLIENT_SYSTEM

print(f"\n① 报表实收 - ② 支付轴真值     = {fmt(d12)}")
print(f"① 报表实收 - ③ 客户系统行     = {fmt(d13)}")
print(f"① 报表实收 - ④ 客户账上       = {fmt(d14)}  <-- 回答「报表是否少了 2.7w」")

print("\n--- 外卖口径差 (报表 price×qty  vs  支付轴/客户 platform_total) ---")
to_gap = takeout_platform_total - takeout_actual_total
print(f"外卖 platform_total (整单)     = {fmt(takeout_platform_total)}")
print(f"外卖 actual (商品行 price×qty)  = {fmt(takeout_actual_total)}")
print(f"platform_total - 商品行        = {fmt(to_gap)}  (配送费/包装费/平台加价, platform_total 通常更高)")

print("\n--- 用外卖 platform_total 替换商品行后, 报表口径会变成 ---")
report_if_platform = dine_actual_total + takeout_platform_total
print(f"堂食 actual + 外卖 platform_total = {fmt(report_if_platform)}")
print(f"   vs ② 支付轴真值 {fmt(PAY_AXIS_TRUTH)}  差 = {fmt(report_if_platform - PAY_AXIS_TRUTH)}")
print(f"   vs ④ 客户账上   {fmt(CLIENT_BANK)}  差 = {fmt(report_if_platform - CLIENT_BANK)}")
