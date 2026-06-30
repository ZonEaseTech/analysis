"""ttpos「测试营业时段」过滤 — 跟 ttpos-server-go 后台报表口径对齐。

来源: ttpos-server-go/main/app/repository/common.go:836 ExcludeTestBusinessByBillSQL
  ttpos 后台 statistics.go:1991 算 actual_sale_amount 时, 会用 NOT EXISTS 排除
  sale_bill.create_time 落在 ttpos_business_status_period [start, end] 区间内的记录
  (店长在 POS 后台开「测试营业」开关期间的订单不算正式营业额)。

本工具:
  - get_stores_with_test_business(client, store_uuids): 返回有这张表的 dataset 集合
  - dine_test_business_clause(): 堂食侧 NOT EXISTS 子句 (基于 sale_bill.create_time)
  - takeout_test_business_clause(): 外卖侧 NOT EXISTS 子句 (基于 takeout_order.create_time)
"""
from functools import lru_cache


@lru_cache(maxsize=1)
def _cached_stores_with_test_business(client_id: int, store_uuids_key: tuple) -> frozenset:
    """内部缓存层 — client_id 作 key 避免跨 client 串味"""
    return frozenset()


def get_stores_with_test_business(client, store_uuids) -> set:
    """返回 53 店里有 ttpos_business_status_period 表的 store_uuid 集合 (按表存在性)。

    BigQuery client.get_table() 不支持批量, 走 53 次轻量 metadata 调用 (~2-3s).
    用 lru_cache 缓存一次 client 内的结果。
    """
    project = client.project
    result = set()
    for u in store_uuids:
        try:
            client.get_table(f"{project}.shop{u}.ttpos_business_status_period")
            result.add(u)
        except Exception:
            pass
    return result


def dine_test_business_clause(sale_bill_alias: str = "sb") -> str:
    """堂食 NOT EXISTS 子句: 基于 sale_bill.create_time 落在测试营业期内。

    需要确保 SQL 已 JOIN ttpos_sale_bill (别名 sb), 并有占位符 {project}/{dataset}。
    """
    return f"""AND NOT EXISTS (
    SELECT 1 FROM `{{project}}`.`{{dataset}}`.`ttpos_business_status_period` bsp
    WHERE bsp.delete_time = 0
      AND {sale_bill_alias}.create_time >= bsp.start_time
      AND (bsp.end_time = 0 OR {sale_bill_alias}.create_time <= bsp.end_time)
  )"""


def takeout_test_business_clause(takeout_order_alias: str = "t") -> str:
    """外卖 NOT EXISTS 子句: 基于 takeout_order.create_time 落在测试营业期内。"""
    return f"""AND NOT EXISTS (
    SELECT 1 FROM `{{project}}`.`{{dataset}}`.`ttpos_business_status_period` bsp
    WHERE bsp.delete_time = 0
      AND {takeout_order_alias}.create_time >= bsp.start_time
      AND (bsp.end_time = 0 OR {takeout_order_alias}.create_time <= bsp.end_time)
  )"""
