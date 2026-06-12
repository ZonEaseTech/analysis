#!/usr/bin/env python3
# 谁问的: 何伟涛 (老板/客户对账) / 2026-06-12
# 问什么: ttpos「成本表(营业额)」29M 比「营业额汇总表」28.89M 多约12万, 为什么?
# 结论:   同源精确桥, 差 = 优惠券 - 退货口径差 - 外卖口径差 + 抹零, 残差抹零级.
#         主因=顾客优惠券(coupon_amount). 经 Fable 复审 7/10, 本脚本补逐店残差+回归.
#
# 口径(全 BQ 同源, 三表不重叠: statistics_sale 0 笔外卖, 外卖独立走 takeout_order):
#   成本表 = SP_revenue(cost_profit.go: final_price×(num-refund), 免单赠送=0) + 外卖 subtotal(price×qty)
#   汇总表 = SS_received(statistics.go: payment_amount - refund_amount - payment_balance) + 外卖 platform_total
#   桥(完备版, 经2026-04回归补全): 差 = coupon + 其他营销折扣(活动/会员/自定义/抹零)
#        - (商品退款 sale_price×refund_num - 支付退款 refund_amount) + (外卖 sub - platform_total)
#   注: 仅 coupon 时5月残差116/4月残差9949; 加"其他折扣"后两月均降到抹零级(4月暴露的activity 9519).
#   ⚠️ other桶含 gift_amount/pay_points_amount, 但成本表口径已用 IF(free|give,0,..) 剔除免单赠送行;
#      当前两者≈0故未被样本检验, 若某月搞整单赠送/积分抵扣可能双算, 需先验。
#
# 用法: venv/bin/python scripts/adhoc/recon_cost_vs_summary_bridge.py 2026-05
import sys, csv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID
from semantic.dimensions.time import month_to_ts_range

month = sys.argv[1] if len(sys.argv) > 1 else '2026-05'
s, e = month_to_ts_range(month)
setup_proxy(); client = get_bq_client()
uuids = [r['商家ID'].strip() for r in csv.DictReader(
    open('resources/wallace.20260525/华莱士商家60家ID.csv', encoding='utf-8-sig'))]

T = dict(sp_rev=0, sp_prodrefund=0, ss_recv=0, ss_refund=0, ss_balance=0, to_sub=0, to_ptot=0, coupon=0, other_disc=0)
per_store = []
for u in uuids:
    d = f"shop{u}"
    try:
        r1 = list(client.query(f"""SELECT
          IFNULL(SUM(IF(free_num>0 OR give_num>0,0, product_final_price*(product_num-refund_num))),0) rev,
          IFNULL(SUM(product_sale_price*refund_num),0) prodrefund
          FROM `{PROJECT_ID}`.`{d}`.`ttpos_statistics_product` WHERE complete_time>={s} AND complete_time<{e}""").result())[0]
        r2 = list(client.query(f"""SELECT
          IFNULL(SUM(payment_amount-refund_amount-payment_balance),0) recv,
          IFNULL(SUM(refund_amount),0) rf, IFNULL(SUM(payment_balance),0) bal
          FROM `{PROJECT_ID}`.`{d}`.`ttpos_statistics_sale` WHERE complete_time>={s} AND complete_time<{e} AND delete_time=0""").result())[0]
        r3 = list(client.query(f"""SELECT
          IFNULL(SUM(IF(order_state IN(10,20,30,40),subtotal,0)),0) sub,
          IFNULL(SUM(IF(order_state IN(10,20,30,40),platform_total,0)),0) ptot
          FROM `{PROJECT_ID}`.`{d}`.`ttpos_takeout_order` WHERE delete_time=0 AND accepted_time>0 AND
            ((order_state=40 AND completed_time>={s} AND completed_time<{e}) OR (order_state IN(10,20,30) AND accepted_time>={s} AND accepted_time<{e}))""").result())[0]
        r4 = list(client.query(f"""SELECT IFNULL(SUM(coupon_amount),0) c,
          IFNULL(SUM(member_discount_fee+custom_discount_fee+activity_amount+gift_amount+pay_points_amount+zero_checkout_fee),0) other
          FROM `{PROJECT_ID}`.`{d}`.`ttpos_sale_order`
          WHERE uuid IN (SELECT DISTINCT sale_order_uuid FROM `{PROJECT_ID}`.`{d}`.`ttpos_statistics_product` WHERE complete_time>={s} AND complete_time<{e})""").result())[0]
        cp = float(r4.c or 0); other = float(r4.other or 0)
        v = dict(sp_rev=float(r1.rev or 0), sp_prodrefund=float(r1.prodrefund or 0),
                 ss_recv=float(r2.recv or 0), ss_refund=float(r2.rf or 0), ss_balance=float(r2.bal or 0),
                 to_sub=float(r3.sub or 0), to_ptot=float(r3.ptot or 0), coupon=cp, other_disc=other)
        for k in T: T[k] += v[k]
        cost = v['sp_rev'] + v['to_sub']; summ = v['ss_recv'] + v['to_ptot']
        pred = v['coupon'] + v['other_disc'] - (v['sp_prodrefund'] - v['ss_refund']) + (v['to_sub'] - v['to_ptot'])
        per_store.append((u, cost - summ, pred, (cost - summ) - pred))
    except Exception as ex:
        print(f"{d} ERR {str(ex)[:50]}")

cost = T['sp_rev'] + T['to_sub']; summ = T['ss_recv'] + T['to_ptot']; diff = cost - summ
refund_gap = T['sp_prodrefund'] - T['ss_refund']; takeout_gap = T['to_sub'] - T['to_ptot']
pred = T['coupon'] + T['other_disc'] - refund_gap + takeout_gap
print(f"=== {month} 成本表 vs 营业额汇总 精确桥 (60店, BQ同源) ===")
print(f"  成本表 = {cost:,.2f}  汇总表 = {summ:,.2f}  差 = {diff:,.2f}")
print(f"  桥: 券 {T['coupon']:,.2f} + 其他折扣 {T['other_disc']:,.2f} − 退货口径差 {refund_gap:,.2f} + 外卖口径差 {takeout_gap:,.2f} = {pred:,.2f}")
print(f"  残差 = {diff - pred:,.2f}")
# 逐店残差分布 (Fable: 防正负抵消)
res = sorted(per_store, key=lambda x: abs(x[3]), reverse=True)
print(f"\n  逐店残差 |绝对值| top5 (验证非正负对冲):")
for u, d_, p_, r_ in res[:5]:
    print(f"    shop…{u[-6:]}  差={d_:>10,.2f}  桥预测={p_:>10,.2f}  残差={r_:>8,.2f}")
print(f"  逐店残差绝对值合计 = {sum(abs(x[3]) for x in per_store):,.2f}  (净残差 {diff-pred:,.2f})")
print(f"  → 绝对值合计 ≈ 净残差 则无大额对冲; 远大于则有")
