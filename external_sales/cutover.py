"""查 BQ 各店最早 complete_time → 切换日期.

外部源(弧酷)的销售用 cutover_dates 切割: 当天起 BQ 接手, 当天前外部源生效.
"""
from __future__ import annotations

import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple


def query_cutover_dates(client, merchants: List[Tuple],
                        store_codes: List[str]) -> Dict[str, datetime.date]:
    """并发查 BQ 各店 ttpos_statistics_product 最早 complete_time.

    Args:
        client: BigQuery client
        merchants: [(account, uuid, store_num, store_name), ...]
        store_codes: 关心的 BQ 店编集合 (3 位字符串)

    Returns:
        {store_code: cutover_date}  — 若 BQ 整表 0 行该店不在返回值中
                                      (表示外部源全月数据全部生效)
    """
    targets = [m for m in merchants if str(m[2]).zfill(3) in set(store_codes)]
    if not targets:
        return {}

    def _check(m):
        acc, uuid, code, _name = m
        sql = (
            f"SELECT MIN(complete_time) AS min_ts "
            f"FROM `{client.project}.shop{uuid}.ttpos_statistics_product` "
            f"WHERE complete_time > 0"
        )
        try:
            row = list(client.query(sql).result())[0]
            return str(code).zfill(3), row.min_ts
        except Exception:
            return str(code).zfill(3), None

    out = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for f in as_completed([ex.submit(_check, m) for m in targets]):
            code, ts = f.result()
            if ts:
                out[code] = datetime.datetime.fromtimestamp(ts).date()
    return out
