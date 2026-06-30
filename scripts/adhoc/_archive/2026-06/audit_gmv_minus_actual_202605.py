# 谁问的: 老板/何伟涛  /  2026-06-02
# 问什么: 2026-05 总营业额 - 总实收 = 5,891.27 铢, 这个差值由什么减免/损失项构成
# 结论:   营业额-实收 = 折扣+赠送+免单+退款 (金额恒等式); 跑全店逐店拆分定位 5891.27 的范围. 待复盘
"""
营业额 - 实收 差额构成审计 (2026-05).

口径:
  营业额 sales_price = 标价 × 销量
  实收   actual_amount = ttpos CountSale 口径 (赠送/免单归零, 扣退款, 用成交价)
  差额   = refund_amount + free_amount + give_amount + discount_amount  (堂食)
          外卖侧 sales_price == actual_amount (华莱士现状), 不产生差额

输出: 全集团总差额 + 逐店差额拆分, 定位 5,891.27 对应哪个范围.
"""
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.cloud import bigquery
from google.oauth2.credentials import Credentials

from semantic.entities.sale_event import sale_event_cte
from semantic.dimensions.time import month_to_ts_range
from bq_reports.report_sales_period_bq import STORE_LIST, PROJECT_ID

MONTH = "2026-05"
DIFF_PARTS = ["refund_amount", "free_amount", "give_amount", "discount_amount"]
ALL_METRICS = ["sales_price", "actual_amount", "cancelled_amount"] + DIFF_PARTS


def get_creds():
    r = subprocess.run(["gcloud", "auth", "print-access-token"],
                       capture_output=True, text=True, check=True)
    return Credentials(token=r.stdout.strip(),
                       scopes=["https://www.googleapis.com/auth/cloud-platform"])


def build_sql(dataset_id, start_ts, end_ts):
    cte = sale_event_cte()
    metrics = ",\n      ".join(f"SUM(se.{m}) AS {m}" for m in ALL_METRICS)
    return ("WITH " + cte + f"""
SELECT
  se.channel,
  {metrics}
FROM sale_event se
GROUP BY se.channel
""").format(project=PROJECT_ID, dataset=dataset_id,
            start_ts=start_ts, end_ts=end_ts)


def query_store(client, store_num, store_uuid, start_ts, end_ts):
    ds = f"shop{store_uuid}"
    out = []
    try:
        for r in client.query(build_sql(ds, start_ts, end_ts)).result():
            out.append({
                "store_num": store_num,
                "channel": r.channel,
                **{m: float(getattr(r, m) or 0) for m in ALL_METRICS},
            })
    except Exception as e:
        print(f"[{store_num}] FAILED: {e}")
    return out


def main():
    start_ts, end_ts = month_to_ts_range(MONTH)
    client = bigquery.Client(project=PROJECT_ID, credentials=get_creds())
    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(query_store, client, sn, su, start_ts, end_ts): sn
                for sn, su in STORE_LIST}
        for f in as_completed(futs):
            rows.extend(f.result())

    # 集团汇总
    g = {m: 0.0 for m in ALL_METRICS}
    by_channel = {}
    by_store = {}
    for r in rows:
        for m in ALL_METRICS:
            g[m] += r[m]
        ch = by_channel.setdefault(r["channel"], {m: 0.0 for m in ALL_METRICS})
        for m in ALL_METRICS:
            ch[m] += r[m]
        st = by_store.setdefault(r["store_num"], {m: 0.0 for m in ALL_METRICS})
        for m in ALL_METRICS:
            st[m] += r[m]

    diff = g["sales_price"] - g["actual_amount"]
    parts_sum = sum(g[m] for m in DIFF_PARTS)

    print(f"\n{'='*72}")
    print(f"2026-05 全集团 营业额 - 实收 差额构成")
    print(f"{'='*72}")
    print(f"  营业额 sales_price   : {g['sales_price']:>16,.2f}")
    print(f"  实收   actual_amount : {g['actual_amount']:>16,.2f}")
    print(f"  差额   (营业额-实收) : {diff:>16,.2f}")
    print(f"  {'-'*50}")
    print(f"  折扣 discount_amount : {g['discount_amount']:>16,.2f}")
    print(f"  赠送 give_amount     : {g['give_amount']:>16,.2f}")
    print(f"  免单 free_amount     : {g['free_amount']:>16,.2f}")
    print(f"  退款 refund_amount   : {g['refund_amount']:>16,.2f}")
    print(f"  四项合计             : {parts_sum:>16,.2f}")
    print(f"  恒等式残差           : {diff - parts_sum:>16,.2f}  (应≈0)")
    print(f"  [参考] 外卖取消金额  : {g['cancelled_amount']:>16,.2f}  (不计入差额)")

    print(f"\n  --- 按渠道 ---")
    for ch, d in sorted(by_channel.items()):
        print(f"  [{ch:8}] 营业额 {d['sales_price']:>14,.2f}  实收 {d['actual_amount']:>14,.2f}  "
              f"差额 {d['sales_price']-d['actual_amount']:>12,.2f}")

    print(f"\n  --- 逐店差额 (营业额-实收), 降序; 定位 5,891.27 对应哪店 ---")
    store_diffs = []
    for sn, d in by_store.items():
        sd = d["sales_price"] - d["actual_amount"]
        store_diffs.append((sn, sd, d))
    for sn, sd, d in sorted(store_diffs, key=lambda x: -x[1]):
        if sd == 0:
            continue
        print(f"  店 {sn:>3}: 差额 {sd:>12,.2f}  "
              f"= 折{d['discount_amount']:>10,.2f} 赠{d['give_amount']:>9,.2f} "
              f"免{d['free_amount']:>9,.2f} 退{d['refund_amount']:>9,.2f}")

    # 高亮接近 5891.27 的店
    target = 5891.27
    print(f"\n  --- 接近目标 {target} 的范围 ---")
    for sn, sd, d in store_diffs:
        if abs(sd - target) < 1.0:
            print(f"  ★ 店 {sn} 差额 {sd:,.2f} ≈ 目标!")
    print()


if __name__ == "__main__":
    main()
