#!/usr/bin/env python3
"""
报表指标语义校验脚本：API vs BQ

用法:
    cd /home/weifashi/hwt/analysis
    PYTHONPATH=/home/weifashi/hwt/analysis venv/bin/python scripts/validate_report_semantics.py --month 2026-03 --jwt <token>
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import requests
from bq_reports.utils.bq_client import get_bq_client, setup_proxy

BKK_TZ = timezone(timedelta(hours=7))
MERCHANT_UUID = "2947521978368000"
API_BASE = "https://merchant.ttpos.dev/api/v1"


def month_to_ts_range(month_str: str):
    """YYYY-MM → (start_ts, end_ts) in Bangkok timezone."""
    year, month = map(int, month_str.split("-"))
    start = datetime(year, month, 1, 0, 0, 0, tzinfo=BKK_TZ)
    if month == 12:
        end = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=BKK_TZ)
    else:
        end = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=BKK_TZ)
    return int(start.timestamp()), int(end.timestamp())


def fetch_api_data(start_ts: int, end_ts: int, jwt: str):
    """从 API 拉取销售统计（sale_mode=0 = 全部）。"""
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {jwt}",
        "appid": MERCHANT_UUID,
        "version-name": "2.22.12",
    }
    all_items = []
    page_no = 1
    while True:
        url = (
            f"{API_BASE}/shop/statistics/product_sales"
            f"?query_start_time={start_ts}"
            f"&query_end_time={end_ts}"
            f"&sale_mode=0"
            f"&page_no={page_no}"
            f"&page_size=100"
        )
        resp = requests.get(url, headers=headers, timeout=30)
        data = resp.json()
        if data.get("code") != 0:
            print(f"[API Error] {data}", file=sys.stderr)
            break
        items = data["data"]["list"]
        if not items:
            break
        all_items.extend(items)
        if len(items) < 100:
            break
        page_no += 1
    return all_items


def fetch_bq_data(start_ts: int, end_ts: int):
    """从 BQ 拉取堂食 + 外卖合并数据。"""
    setup_proxy()
    client = get_bq_client()
    dataset = f"shop{MERCHANT_UUID}"

    # 堂食
    shop_sql = f"""
    SELECT
      sp.product_package_uuid,
      SUM(sp.product_num) AS qty,
      SUM(IF(sp.free_num > 0 OR sp.give_num > 0, 0,
             sp.product_final_price * (sp.product_num - sp.refund_num))) AS revenue,
      SUM(sp.product_sale_price * sp.product_num) AS origin_revenue,
      SUM(sp.refund_num) AS refund_qty
    FROM `diyl-407103`.`{dataset}`.`ttpos_statistics_product` sp
    WHERE sp.complete_time >= {start_ts}
      AND sp.complete_time < {end_ts}
    GROUP BY sp.product_package_uuid
    """

    # 外卖
    takeout_sql = f"""
    SELECT
      toi.ttpos_product_package_uuid AS product_package_uuid,
      SUM(toi.quantity) AS qty,
      SUM(IF(t.order_state IN (10,20,30,40), toi.price * toi.quantity, 0)) AS revenue,
      SUM(toi.price * toi.quantity) AS origin_revenue,
      0 AS refund_qty
    FROM `diyl-407103`.`{dataset}`.`ttpos_takeout_order_item` toi
    JOIN `diyl-407103`.`{dataset}`.`ttpos_takeout_order` t
      ON t.uuid = toi.takeout_order_uuid AND t.delete_time = 0
    WHERE toi.delete_time = 0
      AND toi.ttpos_product_package_uuid > 0
      AND t.order_state IN (10, 20, 30, 40, 60)
      AND t.accepted_time > 0
      AND (
        (t.order_state = 40 AND t.completed_time >= {start_ts} AND t.completed_time < {end_ts})
        OR (t.order_state != 40 AND t.accepted_time >= {start_ts} AND t.accepted_time < {end_ts})
      )
    GROUP BY toi.ttpos_product_package_uuid
    """

    shop_rows = list(client.query(shop_sql).result())
    takeout_rows = list(client.query(takeout_sql).result())

    combined = {}
    for row in shop_rows:
        combined[row.product_package_uuid] = {
            "qty": float(row.qty or 0),
            "revenue": float(row.revenue or 0),
            "origin_revenue": float(row.origin_revenue or 0),
            "refund_qty": float(row.refund_qty or 0),
        }

    for row in takeout_rows:
        uuid = row.product_package_uuid
        if uuid in combined:
            combined[uuid]["qty"] += float(row.qty or 0)
            combined[uuid]["revenue"] += float(row.revenue or 0)
            combined[uuid]["origin_revenue"] += float(row.origin_revenue or 0)
        else:
            combined[uuid] = {
                "qty": float(row.qty or 0),
                "revenue": float(row.revenue or 0),
                "origin_revenue": float(row.origin_revenue or 0),
                "refund_qty": float(row.refund_qty or 0),
            }

    return combined


def fetch_product_names(uuids):
    """从 BQ 拉取商品名称（中英文）。"""
    if not uuids:
        return {}
    setup_proxy()
    client = get_bq_client()
    dataset = f"shop{MERCHANT_UUID}"
    uuid_list = ",".join(str(u) for u in uuids)
    sql = f"""
    SELECT
      uuid,
      REGEXP_REPLACE(COALESCE(
        JSON_EXTRACT_SCALAR(name, '$.zh'),
        JSON_EXTRACT_SCALAR(name, '$.en'),
        '未知'
      ), r'^\\s+|\\s+$', '') AS product_name
    FROM `diyl-407103`.`{dataset}`.`ttpos_product_package`
    WHERE uuid IN ({uuid_list})
    """
    return {row.uuid: row.product_name for row in client.query(sql).result()}


def compare(api_items, bq_data, product_names):
    """详细对比 API 与 BQ 数据（按 qty+revenue 匹配）。"""
    # API 按 (qty, revenue) 索引
    api_by_key = {}
    api_duplicates = []
    for item in api_items:
        key = (item["sales_num"], item["total_pay_price"])
        if key in api_by_key:
            api_duplicates.append((item["product_name"], api_by_key[key]["product_name"]))
        api_by_key[key] = item

    # BQ 按 (qty, revenue) 索引
    bq_by_key = {}
    bq_duplicates = []
    for uuid, data in bq_data.items():
        key = (round(data["qty"], 2), round(data["revenue"], 2))
        name = product_names.get(uuid, f"UUID:{uuid}")
        if key in bq_by_key:
            bq_duplicates.append((name, bq_by_key[key]["name"]))
        bq_by_key[key] = {"uuid": uuid, "name": name, **data}

    # 匹配
    matched = []
    api_only = []
    bq_only = []
    mismatched = []

    for key, api_item in api_by_key.items():
        if key in bq_by_key:
            bq_item = bq_by_key[key]
            matched.append({
                "api_name": api_item["product_name"],
                "bq_name": bq_item["name"],
                "uuid": bq_item["uuid"],
                "qty": api_item["sales_num"],
                "api_revenue": api_item["total_pay_price"],
                "bq_revenue": bq_item["revenue"],
                "api_origin": api_item["original_sales_price"],
                "bq_origin": bq_item["origin_revenue"],
                "api_sales_price": api_item.get("sales_price", api_item["total_pay_price"]),
            })
        else:
            api_only.append(api_item)

    for key, bq_item in bq_by_key.items():
        if key not in api_by_key:
            bq_only.append(bq_item)

    return {
        "matched": matched,
        "api_only": api_only,
        "bq_only": bq_only,
        "api_duplicates": api_duplicates,
        "bq_duplicates": bq_duplicates,
    }


def print_report(results, api_items, bq_data):
    """打印校验报告。"""
    matched = results["matched"]
    api_only = results["api_only"]
    bq_only = results["bq_only"]
    api_dups = results["api_duplicates"]
    bq_dups = results["bq_duplicates"]

    total_api = len(api_items)
    total_bq = len(bq_data)

    print("=" * 90)
    print("报表指标语义校验报告：API vs BQ")
    print("=" * 90)
    print(f"\n【总体统计】")
    print(f"  API 商品数: {total_api}")
    print(f"  BQ  商品数: {total_bq}")
    print(f"  ✅ 完全匹配(qty+revenue): {len(matched)}")
    print(f"  ❌ API 独有: {len(api_only)}")
    print(f"  ❌ BQ  独有: {len(bq_only)}")
    if api_dups:
        print(f"  ⚠️  API 重复键(qty+revenue相同): {len(api_dups)} 对")
    if bq_dups:
        print(f"  ⚠️  BQ  重复键(qty+revenue相同): {len(bq_dups)} 对")

    # 检查 sales_price vs total_pay_price 差异
    discount_items = [m for m in matched if m["api_sales_price"] != m["api_revenue"]]
    if discount_items:
        print(f"\n【折扣商品】sales_price ≠ total_pay_price: {len(discount_items)} 项")
        for m in discount_items[:5]:
            diff = m["api_sales_price"] - m["api_revenue"]
            print(f"  {m['api_name']}: sales_price={m['api_sales_price']}, pay={m['api_revenue']}, diff={diff}")

    # 检查 original_sales_price 与 BQ origin_revenue 差异
    origin_mismatch = []
    for m in matched:
        if abs(m["api_origin"] - round(m["bq_origin"], 2)) > 0.01:
            origin_mismatch.append(m)
    if origin_mismatch:
        print(f"\n【原价不匹配】original_sales_price ≠ BQ origin_revenue: {len(origin_mismatch)} 项")
        for m in origin_mismatch[:5]:
            print(f"  {m['api_name']}: API={m['api_origin']}, BQ={m['bq_origin']}")

    if api_only:
        print(f"\n【API 独有（未在 BQ 匹配到相同 qty+revenue）】前 5 项")
        for item in api_only[:5]:
            print(f"  {item['product_name']}: num={item['sales_num']}, pay={item['total_pay_price']}, origin={item['original_sales_price']}")

    if bq_only:
        print(f"\n【BQ 独有（未在 API 匹配到相同 qty+revenue）】前 5 项")
        for item in bq_only[:5]:
            print(f"  {item['name']}: qty={item['qty']}, revenue={item['revenue']}, origin={item['origin_revenue']}")

    print("\n" + "=" * 90)
    print("【指标语义验证结论】")
    print("=" * 90)

    if len(matched) == total_api == total_bq and not api_only and not bq_only:
        print("\n✅ API 与 BQ 数据完全一致！")
        print("\n  指标语义对齐：")
        print("    API sales_num           = BQ SUM(product_num)")
        print("    API total_pay_price     = BQ SUM(product_final_price × (num - refund_num))")
        print("    API original_sales_price ≈ BQ SUM(product_sale_price × num)")
        print("    API sales_price         = 标价 × 销量（扣折扣前）")
        if discount_items:
            print(f"\n  ⚠️  {len(discount_items)} 项商品存在折扣（sales_price > total_pay_price）")
    else:
        match_rate = len(matched) / max(total_api, total_bq) * 100
        print(f"\n⚠️  匹配率: {match_rate:.1f}% ({len(matched)}/{max(total_api, total_bq)})")
        if api_only:
            print(f"   API 独有: {len(api_only)} 项（可能是新商品或数据不同步）")
        if bq_only:
            print(f"   BQ  独有: {len(bq_only)} 项（可能是已下架商品）")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", default="2026-03", help="月份 YYYY-MM")
    parser.add_argument("--jwt", required=True, help="JWT Token")
    args = parser.parse_args()

    start_ts, end_ts = month_to_ts_range(args.month)
    print(f"校验月份: {args.month}")
    print(f"时间范围: {start_ts} ~ {end_ts}")
    print(f"对应 UTC: {datetime.fromtimestamp(start_ts, tz=BKK_TZ)} ~ {datetime.fromtimestamp(end_ts, tz=BKK_TZ)}")
    print()

    print("[1/4] 从 API 拉取销售统计...")
    api_items = fetch_api_data(start_ts, end_ts, args.jwt)
    print(f"      获取 {len(api_items)} 条记录")

    print("[2/4] 从 BQ 拉取堂食+外卖数据...")
    bq_data = fetch_bq_data(start_ts, end_ts)
    print(f"      获取 {len(bq_data)} 条记录")

    print("[3/4] 拉取商品名称映射...")
    all_uuids = list(bq_data.keys())
    product_names = fetch_product_names(all_uuids)
    print(f"      获取 {len(product_names)} 个商品名称")

    print("[4/4] 执行对比...\n")
    results = compare(api_items, bq_data, product_names)
    print_report(results, api_items, bq_data)


if __name__ == "__main__":
    main()
