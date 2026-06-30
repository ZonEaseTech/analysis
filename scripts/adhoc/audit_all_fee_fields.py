"""2026-02 真正穷举: 所有可能成为差额的金额字段, 包括 sale_bill / sale_order /
sale_order_product / sale_order_coupon 上每一个 NUMERIC fee 类字段。"""
import sys, csv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bq_reports.utils.bq_client import get_bq_client, setup_proxy, PROJECT_ID, STORE_UUIDS

setup_proxy(); c = get_bq_client()

S = "UNIX_SECONDS(TIMESTAMP('2026-02-01 00:00:00+07'))"
E = "UNIX_SECONDS(TIMESTAMP('2026-03-01 00:00:00+07'))"

# Part 1: statistics_product 上的 flavor_price / sauce_price (没算过!)
print("=== A) statistics_product 的 flavor_price / sauce_price (商品行附加费) ===")
unions = []
for u in STORE_UUIDS:
    unions.append(f"""
SELECT
  SUM(IF(sp.free_num>0 OR sp.give_num>0, 0, sp.flavor_price * (sp.product_num - sp.refund_num))) AS flavor_x_qty,
  SUM(IF(sp.free_num>0 OR sp.give_num>0, 0, sp.sauce_price  * (sp.product_num - sp.refund_num))) AS sauce_x_qty,
  SUM(sp.flavor_price) AS flavor_raw,
  SUM(sp.sauce_price) AS sauce_raw
FROM `{PROJECT_ID}.shop{u}.ttpos_statistics_product` sp
WHERE sp.complete_time >= {S} AND sp.complete_time < {E}
""")
sql = f"WITH a AS ({' UNION ALL '.join(unions)}) SELECT ROUND(SUM(flavor_x_qty),2) fx, ROUND(SUM(sauce_x_qty),2) sx, ROUND(SUM(flavor_raw),2) fr, ROUND(SUM(sauce_raw),2) sr FROM a"
for r in c.query(sql).result():
    print(f"  SUM(flavor_price * net_qty) = {r['fx'] or 0:,.2f}")
    print(f"  SUM(sauce_price * net_qty)  = {r['sx'] or 0:,.2f}")
    print(f"  SUM(flavor_price) raw       = {r['fr'] or 0:,.2f}  ← 加总字段值, 不乘数量")
    print(f"  SUM(sauce_price) raw        = {r['sr'] or 0:,.2f}")

# Part 2: sale_bill 上所有 fee 字段 (堂食账单粒度)
print("\n=== B) sale_bill 上所有 fee 字段 (finish_time 落 2 月, status=1, 不 hide/delete) ===")
unions = []
for u in STORE_UUIDS:
    unions.append(f"""
SELECT service_fee, tax_fee, custom_discount_fee, member_discount_fee,
       gift_amount, activity_amount, free_amount, payment_commission_fee
FROM `{PROJECT_ID}.shop{u}.ttpos_sale_bill`
WHERE finish_time >= {S} AND finish_time < {E}
  AND delete_time = 0 AND hide_bill_time = 0 AND status = 1
""")
sql = f"""WITH a AS ({' UNION ALL '.join(unions)}) SELECT
  ROUND(SUM(service_fee),2) AS service_fee,
  ROUND(SUM(tax_fee),2) AS tax_fee,
  ROUND(SUM(custom_discount_fee),2) AS custom_discount_fee,
  ROUND(SUM(member_discount_fee),2) AS member_discount_fee,
  ROUND(SUM(gift_amount),2) AS gift_amount,
  ROUND(SUM(activity_amount),2) AS activity_amount,
  ROUND(SUM(free_amount),2) AS free_amount,
  ROUND(SUM(payment_commission_fee),2) AS payment_commission_fee
FROM a"""
for r in c.query(sql).result():
    for k in r.keys():
        v = r[k] or 0
        marker = "  ⚠️ 量级匹配 ~14k" if 8000 < abs(v) < 25000 else ""
        print(f"  {k:<25} {v:>14,.2f}{marker}")

# Part 3: sale_order 上所有 fee 字段
print("\n=== C) sale_order 上所有 fee 字段 (finish_time 2 月) ===")
unions = []
for u in STORE_UUIDS:
    unions.append(f"""
SELECT member_discount_fee, custom_discount_fee, zero_fee,
       service_fee, tax_fee, custom_amount, pay_points_amount, coupon_amount,
       activity_amount, change_amount, zero_checkout_fee,
       payment_commission_fee, gift_amount, member_balance, erp_discount_amount
FROM `{PROJECT_ID}.shop{u}.ttpos_sale_order`
WHERE finish_time >= {S} AND finish_time < {E} AND delete_time = 0
""")
fields = ['member_discount_fee','custom_discount_fee','zero_fee','service_fee','tax_fee',
          'custom_amount','pay_points_amount','coupon_amount','activity_amount','change_amount',
          'zero_checkout_fee','payment_commission_fee','gift_amount','member_balance','erp_discount_amount']
selects = ", ".join([f"ROUND(SUM({f}),2) AS {f}" for f in fields])
sql = f"WITH a AS ({' UNION ALL '.join(unions)}) SELECT {selects} FROM a"
for r in c.query(sql).result():
    for k in fields:
        v = r[k] or 0
        marker = "  ⚠️ 量级匹配 ~14k" if 8000 < abs(v) < 25000 else ""
        print(f"  {k:<25} {v:>14,.2f}{marker}")

# Part 4: sale_order_coupon (优惠券金额) - 用券抵扣
print("\n=== D) sale_order_coupon 优惠券抵扣 (sale_order 落 2 月) ===")
unions = []
for u in STORE_UUIDS:
    unions.append(f"""
SELECT soc.coupon_amount, soc.coupon_origin_amount
FROM `{PROJECT_ID}.shop{u}.ttpos_sale_order_coupon` soc
JOIN `{PROJECT_ID}.shop{u}.ttpos_sale_order` so ON so.uuid = soc.sale_order_uuid
WHERE so.finish_time >= {S} AND so.finish_time < {E} AND so.delete_time = 0
""")
sql = f"WITH a AS ({' UNION ALL '.join(unions)}) SELECT ROUND(SUM(coupon_amount),2) ca, ROUND(SUM(coupon_origin_amount),2) co FROM a"
for r in c.query(sql).result():
    print(f"  coupon_amount        = {r['ca'] or 0:,.2f}")
    print(f"  coupon_origin_amount = {r['co'] or 0:,.2f}")
