#!/usr/bin/env python3
# 谁问的: 何伟涛 / 2026-05-28
# 问什么: 量化系统比客户实收多记的 2.7 万(净 +27,426.3)来源, 全部从自有 BQ 数据拆
# 结论: 自有数据只能解释 ~8.4k(外卖时间边界净)+218(隐藏单),且现金抹零方向相反(-10.4k).
#       外卖系统多记 ~43k 的「在途/跨月」假说证伪(实测净仅 +8.4k).
#       2.7 万里能用自有数据定性解释的不足 1/3, 大头(外卖平台抽佣/收单换标签净 -18.5k)
#       必须靠银行流水 + 平台结算单, BQ 查不了.
# 注: 79 配置门店中 17 个是 dormant 空壳(无 takeout/stat_payment/sale_bill 表), 已确认零贡献, 正确跳过.
"""
逐项量化 2026-04 全门店「系统可能多记」的来源:
 1. 外卖月底在途 / 时间边界 (grab/lineman): 4月接单5月完成 / 4月底在途 / 3月接单4月完成
 2. 退款跨月时点差 (ttpos_statistics_payment refund_amount)
 3. 现金抹零 (Cash payment_amount vs sale_bill 应收)
 4. 作废单 (status=2) / 隐藏单 (hide_bill_time!=0) 残留支付
每项给全门店合计金额 + 笔数 + 方向。
"""
import sys
sys.path.insert(0, '/home/weifashi/hwt/analysis')

from collections import defaultdict
from bq_reports.utils.bq_client import get_bq_client

client = get_bq_client()
BQ_LOCATION = "asia-southeast1"

# 2026-04 BKK 整月
START_TS = 1774976400
END_TS = 1777568400
# 5 月窗口 (查跨月)
MAY_START = 1777568400
MAY_END = 1780246800

def fmt(x):
    return f"{x:,.2f}"

# ========== 门店枚举 ==========
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
    all_th_stores.append({'uuid': str(row.uuid), 'dataset': f"shop{row.uuid}"})

shop_dataset_ids = set(d.dataset_id for d in client.list_datasets() if d.dataset_id.startswith('shop'))
stores = [s for s in all_th_stores if s['dataset'] in shop_dataset_ids]
print(f"TH 门店(配置) {len(all_th_stores)}, 实际存在 dataset {len(stores)}")

# 累加器: 每项 -> {amount, count}, 部分按平台细分
agg = defaultdict(lambda: {'amount': 0.0, 'count': 0})
# 失败计数
fail = defaultdict(int)

def add(key, amount, count):
    agg[key]['amount'] += float(amount) if amount else 0.0
    agg[key]['count'] += int(count) if count else 0

PROJ = "diyl-407103"

for s in stores:
    ds = s['dataset']

    # ---------- 1. 外卖时间边界 (grab/lineman) ----------
    # (a) 4月内 accepted 但 5月才 completed (state=40, completed in May)
    # (b) 4月底 accepted 但仍未完成 (state != 40, accepted in Apr)
    # (c) 3月 accepted, 4月 completed
    try:
        job = client.query(f"""
            SELECT
              LOWER(IFNULL(platform,'(null)')) AS plat,
              -- a: accepted in Apr, state=40, completed in May
              SUM(CASE WHEN order_state=40 AND accepted_time>={START_TS} AND accepted_time<{END_TS}
                        AND completed_time>={MAY_START} AND completed_time<{MAY_END}
                       THEN platform_total ELSE 0 END) AS a_amt,
              SUM(CASE WHEN order_state=40 AND accepted_time>={START_TS} AND accepted_time<{END_TS}
                        AND completed_time>={MAY_START} AND completed_time<{MAY_END}
                       THEN 1 ELSE 0 END) AS a_cnt,
              -- b: accepted in Apr, NOT completed (state in 10,20,30) i.e. still in-flight
              SUM(CASE WHEN order_state IN (10,20,30) AND accepted_time>={START_TS} AND accepted_time<{END_TS}
                       THEN platform_total ELSE 0 END) AS b_amt,
              SUM(CASE WHEN order_state IN (10,20,30) AND accepted_time>={START_TS} AND accepted_time<{END_TS}
                       THEN 1 ELSE 0 END) AS b_cnt,
              -- c: accepted in Mar (before Apr), completed in Apr, state=40
              SUM(CASE WHEN order_state=40 AND accepted_time>0 AND accepted_time<{START_TS}
                        AND completed_time>={START_TS} AND completed_time<{END_TS}
                       THEN platform_total ELSE 0 END) AS c_amt,
              SUM(CASE WHEN order_state=40 AND accepted_time>0 AND accepted_time<{START_TS}
                        AND completed_time>={START_TS} AND completed_time<{END_TS}
                       THEN 1 ELSE 0 END) AS c_cnt
            FROM `{PROJ}`.`{ds}`.`ttpos_takeout_order`
            WHERE delete_time=0
              AND LOWER(platform) IN ('grab','lineman')
            GROUP BY plat
        """, location=BQ_LOCATION)
        for row in job.result():
            p = row.plat
            add(f"1a_acc_apr_comp_may::{p}", row.a_amt, row.a_cnt)
            add(f"1b_acc_apr_inflight::{p}", row.b_amt, row.b_cnt)
            add(f"1c_acc_mar_comp_apr::{p}", row.c_amt, row.c_cnt)
    except Exception as e:
        fail['takeout'] += 1

    # ---------- 2. 退款跨月时点差 ----------
    # ttpos_statistics_payment 无独立退款时间字段, 只有 complete_time/create_time/update_time
    # (2.1) 4月 complete_time 记录里 refund_amount 合计 (系统在4月口径下已扣)
    # (2.2) refund_amount>0 且 update_time 落在5月 (退款动作可能5月才发生) 的 4月支付记录
    try:
        job = client.query(f"""
            SELECT
              SUM(CASE WHEN refund_amount>0 THEN refund_amount ELSE 0 END) AS r_amt,
              SUM(CASE WHEN refund_amount>0 THEN 1 ELSE 0 END) AS r_cnt,
              SUM(CASE WHEN refund_amount>0 AND update_time>={MAY_START} AND update_time<{MAY_END}
                       THEN refund_amount ELSE 0 END) AS r_may_amt,
              SUM(CASE WHEN refund_amount>0 AND update_time>={MAY_START} AND update_time<{MAY_END}
                       THEN 1 ELSE 0 END) AS r_may_cnt
            FROM `{PROJ}`.`{ds}`.`ttpos_statistics_payment`
            WHERE delete_time=0
              AND complete_time>={START_TS} AND complete_time<{END_TS}
        """, location=BQ_LOCATION)
        for row in job.result():
            add("2_refund_apr_total", row.r_amt, row.r_cnt)
            add("2_refund_apr_updated_in_may", row.r_may_amt, row.r_may_cnt)
    except Exception:
        fail['refund'] += 1

    # ---------- 3. 现金抹零 ----------
    # Cash payment 关联 sale_bill, 比较 payment_amount vs sb.amount(应收)
    # sale_bill.uuid INTEGER vs statistics_payment.sale_bill_uuid NUMERIC -> CAST
    try:
        job = client.query(f"""
            WITH cash_pay AS (
              SELECT sp.sale_bill_uuid AS sbu, sp.payment_amount AS pay
              FROM `{PROJ}`.`{ds}`.`ttpos_statistics_payment` sp
              LEFT JOIN `{PROJ}`.`{ds}`.`ttpos_payment_method` pm
                ON pm.uuid = sp.payment_method_uuid
              WHERE sp.delete_time=0
                AND sp.complete_time>={START_TS} AND sp.complete_time<{END_TS}
                AND LOWER(IFNULL(pm.payment_name,'')) LIKE '%cash%'
            )
            SELECT
              SUM(cp.pay - sb.amount) AS diff_sum,
              SUM(CASE WHEN ABS(cp.pay - sb.amount) >= 0.01 THEN 1 ELSE 0 END) AS diff_cnt,
              SUM(CASE WHEN cp.pay - sb.amount > 0.01 THEN cp.pay - sb.amount ELSE 0 END) AS over_amt,
              SUM(CASE WHEN cp.pay - sb.amount > 0.01 THEN 1 ELSE 0 END) AS over_cnt,
              SUM(CASE WHEN cp.pay - sb.amount < -0.01 THEN cp.pay - sb.amount ELSE 0 END) AS under_amt,
              SUM(CASE WHEN cp.pay - sb.amount < -0.01 THEN 1 ELSE 0 END) AS under_cnt
            FROM cash_pay cp
            JOIN `{PROJ}`.`{ds}`.`ttpos_sale_bill` sb
              ON CAST(sb.uuid AS NUMERIC) = cp.sbu AND sb.delete_time=0
        """, location=BQ_LOCATION)
        for row in job.result():
            add("3_cash_round_net", row.diff_sum, row.diff_cnt)
            add("3_cash_round_over(pay>bill)", row.over_amt, row.over_cnt)
            add("3_cash_round_under(pay<bill)", row.under_amt, row.under_cnt)
    except Exception:
        fail['cash'] += 1

    # ---------- 4. 作废单 / 隐藏单残留支付 ----------
    # status=2 (已取消) 关联 payment_amount; hide_bill_time!=0 (隐藏单) 关联 payment_amount
    try:
        job = client.query(f"""
            WITH pay AS (
              SELECT sp.sale_bill_uuid AS sbu, sp.payment_amount AS pay, sp.refund_amount AS ref
              FROM `{PROJ}`.`{ds}`.`ttpos_statistics_payment` sp
              WHERE sp.delete_time=0
                AND sp.complete_time>={START_TS} AND sp.complete_time<{END_TS}
            )
            SELECT
              SUM(CASE WHEN sb.status=2 THEN p.pay ELSE 0 END) AS cancel_amt,
              SUM(CASE WHEN sb.status=2 THEN 1 ELSE 0 END) AS cancel_cnt,
              SUM(CASE WHEN sb.hide_bill_time!=0 THEN p.pay ELSE 0 END) AS hide_amt,
              SUM(CASE WHEN sb.hide_bill_time!=0 THEN 1 ELSE 0 END) AS hide_cnt,
              SUM(CASE WHEN sb.hide_bill_time!=0 THEN p.pay - p.ref ELSE 0 END) AS hide_net_amt
            FROM pay p
            JOIN `{PROJ}`.`{ds}`.`ttpos_sale_bill` sb
              ON CAST(sb.uuid AS NUMERIC) = p.sbu AND sb.delete_time=0
        """, location=BQ_LOCATION)
        for row in job.result():
            add("4_cancelled_status2_pay", row.cancel_amt, row.cancel_cnt)
            add("4_hidden_bill_pay(gross)", row.hide_amt, row.hide_cnt)
            add("4_hidden_bill_pay(net)", row.hide_net_amt, row.hide_cnt)
    except Exception:
        fail['cancel_hide'] += 1

print(f"查询失败统计: {dict(fail)}")

# ========== 输出量化表 ==========
print("\n" + "=" * 92)
print("系统多记来源量化表 (2026-04 全门店)")
print("=" * 92)
print(f"{'来源项':<42}{'金额':>20}{'笔数':>12}{'方向':>14}")
print("-" * 92)

ORDER = [
    ("1a_acc_apr_comp_may::grab",      "1a 接单4月/完成5月 GRAB",       "系统偏高"),
    ("1a_acc_apr_comp_may::lineman",   "1a 接单4月/完成5月 LINEMAN",    "系统偏高"),
    ("1b_acc_apr_inflight::grab",      "1b 4月接单/月末在途 GRAB",      "系统偏高"),
    ("1b_acc_apr_inflight::lineman",   "1b 4月接单/月末在途 LINEMAN",   "系统偏高"),
    ("1c_acc_mar_comp_apr::grab",      "1c 接单3月/完成4月 GRAB",       "口径相关"),
    ("1c_acc_mar_comp_apr::lineman",   "1c 接单3月/完成4月 LINEMAN",    "口径相关"),
    ("2_refund_apr_total",             "2  4月退款合计(已扣)",          "系统偏低"),
    ("2_refund_apr_updated_in_may",    "2  4月支付/5月才更新退款",      "可能跨月"),
    ("3_cash_round_net",               "3  现金抹零净额(pay-bill)",     "看符号"),
    ("3_cash_round_over(pay>bill)",    "3  现金 pay>bill",              "系统偏高"),
    ("3_cash_round_under(pay<bill)",   "3  现金 pay<bill",              "系统偏低"),
    ("4_cancelled_status2_pay",        "4  作废单(status=2)残留支付",   "系统偏高"),
    ("4_hidden_bill_pay(gross)",       "4  隐藏单支付(gross)",          "系统偏高?"),
    ("4_hidden_bill_pay(net)",         "4  隐藏单支付(net=pay-ref)",    "系统偏高?"),
]
for key, label, direction in ORDER:
    v = agg.get(key, {'amount': 0.0, 'count': 0})
    print(f"{label:<42}{fmt(v['amount']):>20}{v['count']:>12,}{direction:>14}")

# ========== 关键聚合 ==========
def g(k):
    return agg.get(k, {'amount':0.0,'count':0})['amount']

print("\n" + "=" * 92)
print("关键小计")
print("=" * 92)

to_a = g("1a_acc_apr_comp_may::grab") + g("1a_acc_apr_comp_may::lineman")
to_b = g("1b_acc_apr_inflight::grab") + g("1b_acc_apr_inflight::lineman")
to_c = g("1c_acc_mar_comp_apr::grab") + g("1c_acc_mar_comp_apr::lineman")
print(f"外卖 1a (4月接/5月完, 系统多算)        = {fmt(to_a)}")
print(f"外卖 1b (4月接/月末在途, 系统多算)      = {fmt(to_b)}")
print(f"外卖 1c (3月接/4月完, 系统少算/应收)    = {fmt(to_c)}")
print(f"外卖时间边界净影响 (1a+1b-1c)           = {fmt(to_a + to_b - to_c)}")
print(f"现金抹零净额 (3)                        = {fmt(g('3_cash_round_net'))}")
print(f"作废单残留 (4)                          = {fmt(g('4_cancelled_status2_pay'))}")
print(f"隐藏单 net (4)                          = {fmt(g('4_hidden_bill_pay(net)'))}")

print("\n基准: 外卖+现金 真正系统多记净额 = +45,896 ; 整体净差 = +27,426.3")
