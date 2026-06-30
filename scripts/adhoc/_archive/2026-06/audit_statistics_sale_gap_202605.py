# 谁问的: 老板/何伟涛  /  2026-06-02
# 问什么: 2026-05 ttpos营业汇总 总营业额-实收=353,476 (人均5891.27), 减免/损失项组成
# 结论:   用 ttpos_statistics_sale 重建 ttpos CountSale 口径, 账单级拆 优惠/会员折扣/赠菜/免单/退款/抹零. 待复盘
"""
ttpos 营业数据汇总 "总营业额 - 实收" 差额构成审计 (2026-05).

口径来源 (ttpos-server-go statistics.go:88-103, CountSale):
  总营业额 sale_amount = product_price + product_tax + service_fee + service_tax
                        + payment_fee + extend_price
  实收     received_amount = payment_amount - refund_amount - payment_balance
  减免桶 (账单级, 存在 ttpos_statistics_sale):
    优惠折扣 discount  = discount - refund_discount
    会员折扣 discount_member
    赠菜    gift_amount
    免单    free_amount
    退款    refund_amount(+refund_payment_balance)
    抹零/余额 payment_balance

目标: 复现 Excel 总营业额 29,241,000.34 / 实收 28,887,524.34 / 差 353,476,
      并把差额拆成上述桶.
"""
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.cloud import bigquery
from google.oauth2.credentials import Credentials

from semantic.dimensions.time import month_to_ts_range
from bq_reports.report_sales_period_bq import STORE_LIST, PROJECT_ID

MONTH = "2026-05"

# 直接对 line 级 SUM (groupby sale_bill 只影响计数, 不影响金额求和)
AGG_SQL = """
SELECT
  '{store_num}' AS store_num,
  -- 总营业额 (ttpos sale_amount)
  SUM(product_price + product_tax + service_fee + service_tax + payment_fee + extend_price) AS gmv,
  -- 实收 (ttpos received_amount)
  SUM(payment_amount - refund_amount - payment_balance) AS received,
  -- 减免/损失桶
  SUM(discount - refund_discount) AS discount,
  SUM(discount_member - refund_discount_member) AS discount_member,
  SUM(gift_amount) AS gift_amount,
  SUM(free_amount) AS free_amount,
  SUM(refund_amount + refund_payment_balance) AS refund_amount,
  SUM(payment_balance) AS payment_balance,
  -- 参考: 税/服务费/手续费 (华莱士应为 0)
  SUM(product_tax + service_tax) AS tax,
  SUM(service_fee) AS service_fee,
  SUM(payment_fee) AS payment_fee,
  SUM(product_price) AS product_price,
  SUM(payment_amount) AS payment_amount,
  SUM(IF(is_takeout=1, 1, 0)) AS takeout_lines,
  SUM(IF(is_takeout=0, 1, 0)) AS dine_lines
FROM `{project}`.`{dataset}`.`ttpos_statistics_sale`
WHERE complete_time >= {start_ts} AND complete_time < {end_ts}
"""

METRICS = ["gmv", "received", "discount", "discount_member", "gift_amount",
           "free_amount", "refund_amount", "payment_balance", "tax",
           "service_fee", "payment_fee", "product_price", "payment_amount",
           "takeout_lines", "dine_lines"]


def get_creds():
    r = subprocess.run(["gcloud", "auth", "print-access-token"],
                       capture_output=True, text=True, check=True)
    return Credentials(token=r.stdout.strip(),
                       scopes=["https://www.googleapis.com/auth/cloud-platform"])


def query_store(client, store_num, store_uuid, start_ts, end_ts):
    sql = AGG_SQL.format(store_num=store_num, project=PROJECT_ID,
                         dataset=f"shop{store_uuid}",
                         start_ts=start_ts, end_ts=end_ts)
    try:
        for r in client.query(sql).result():
            return {"store_num": store_num,
                    **{m: float(getattr(r, m) or 0) for m in METRICS}}
    except Exception as e:
        print(f"[{store_num}] FAILED: {e}")
    return None


def main():
    start_ts, end_ts = month_to_ts_range(MONTH)
    client = bigquery.Client(project=PROJECT_ID, credentials=get_creds())
    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(query_store, client, sn, su, start_ts, end_ts): sn
                for sn, su in STORE_LIST}
        for f in as_completed(futs):
            r = f.result()
            if r:
                rows.append(r)

    g = {m: 0.0 for m in METRICS}
    for r in rows:
        for m in METRICS:
            g[m] += r[m]

    gap = g["gmv"] - g["received"]
    buckets = ["discount", "discount_member", "gift_amount", "free_amount",
               "refund_amount", "payment_balance"]
    bucket_sum = sum(g[b] for b in buckets)

    P = lambda k, v: print(f"  {k:24}: {v:>16,.2f}")
    print(f"\n{'='*64}")
    print(f"2026-05 ttpos营业汇总口径 ({len(rows)} 店)")
    print(f"{'='*64}")
    P("总营业额 gmv", g["gmv"])
    print(f"  (Excel 对照: 29,241,000.34  差 {g['gmv']-29241000.34:+,.2f})")
    P("实收 received", g["received"])
    print(f"  (Excel 对照: 28,887,524.34  差 {g['received']-28887524.34:+,.2f})")
    P("差额 (总营业额-实收)", gap)
    print(f"  (Excel 对照: 353,476.00  人均 5,891.27)")
    print(f"  {'-'*46}")
    print("  >>> 减免/损失项构成 <<<")
    P("优惠折扣 discount", g["discount"])
    P("会员折扣 discount_member", g["discount_member"])
    P("赠菜 gift_amount", g["gift_amount"])
    P("免单 free_amount", g["free_amount"])
    P("退款 refund_amount", g["refund_amount"])
    P("抹零/余额 payment_balance", g["payment_balance"])
    P("六项合计", bucket_sum)
    P("差额-六项 残差", gap - bucket_sum)
    print(f"  {'-'*46}")
    print("  >>> 口径校验 (华莱士应为 0) <<<")
    P("税 tax", g["tax"])
    P("服务费 service_fee", g["service_fee"])
    P("支付手续费 payment_fee", g["payment_fee"])
    print()


if __name__ == "__main__":
    main()
