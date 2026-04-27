#!/usr/bin/env python3
"""
利润报表导出 —— 套餐/单品利润分析（配置驱动版，聚合优化版）

优化点：
1. 订单与 BOM 分两次查询，消除 JOIN 膨胀
2. 订单在 BQ 内聚合，大幅减少传输数据量
3. 套餐结构独立查询并缓存

Usage:
    python -m bq_reports.profit_margin_report --month 2026-03 --output exports/profit_202603.xlsx --use-erp-price
"""

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# 跟 ttpos 业务时区对齐（曼谷 +07:00），月份边界以 BKK 时间为准
BKK_TZ = timezone(timedelta(hours=7))
from pathlib import Path

from bq_reports.utils.bq_client import setup_proxy
from utils.cache import get_cache, set_cache, cache_key
from utils.report_engine import ReportEngine, load_sheet_config
from utils.resource_adapter import get_adapter


# ============================================================================
# 门店名称映射加载
# ============================================================================

def _load_store_names(config: dict = None):
    """从配置中指定的资源加载门店编号→名称映射。支持缓存。"""
    cfg = config or {}
    mapping_config = cfg.get("store_name_mapping")
    if not mapping_config:
        return {}

    cache_ttl = cfg.get("cache", {}).get("store_names_ttl", 604800)
    # v2: 同时存原值与去前导 0 的归一化 key（"001" 与 "1" 都能匹配）
    key = cache_key("store_names_v2", {"path": mapping_config.get("path", "")})
    cached = get_cache(key, ttl_seconds=cache_ttl)
    if cached is not None:
        print(f"[Store Names] 缓存命中: {len(cached)} 个")
        return cached

    try:
        adapter = get_adapter(mapping_config["adapter"])
        records = adapter.load(mapping_config)
        mapping = {}
        for r in records:
            num = r.get("store_number")
            name = r.get("store_name")
            if num is None or not name:
                continue
            s = str(num).strip()
            if not s:
                continue
            clean_name = str(name).strip()
            mapping[s] = clean_name
            # 数字编号同时存归一化版本，避免 "001" / "1" 互不匹配
            try:
                mapping[str(int(s))] = clean_name
            except ValueError:
                pass
        set_cache(key, mapping)
        # 用去重数计真实门店数
        unique_names = len(set(mapping.values()))
        print(f"[Store Names] 加载 {unique_names} 个门店名称（{len(mapping)} 个 key）")
        return mapping
    except Exception as e:
        print(f"[警告] 加载门店名称失败: {e}")
        return {}


# ============================================================================
# ERPNext 价格加载（带缓存包装）
# ============================================================================

def _try_load_erp_prices(price_list: str = None, cache_ttl: int = 3600):
    try:
        from bq_reports.utils.erpnext_api import load_erpnext_prices
        key = cache_key("erpnext_prices", {"price_list": price_list or "Standard Buying"})
        cached = get_cache(key, ttl_seconds=cache_ttl)
        if cached is not None:
            print(f"[ERPNext API] 缓存命中: {len(cached)} 条价格")
            return cached
        prices = load_erpnext_prices(price_list=price_list)
        set_cache(key, prices)
        return prices
    except Exception as e:
        print(f"[警告] 加载 ERPNext 价格失败: {e}")
        return {}


# ============================================================================
# 补充 BOM 加载（适配器 + 缓存）
# ============================================================================

def _load_fallback_boms(config: dict = None):
    """从配置中指定的资源加载补充 BOM 数据。支持缓存。"""
    cfg = config or {}
    cache_ttl = cfg.get("cache", {}).get("fallback_bom_ttl", 86400)
    bom_config = cfg.get("fallback_bom")
    if not bom_config:
        return {}

    # v2: 应用市场 BOM 替换/删除规则
    key = cache_key("fallback_boms_v2", {"path": bom_config.get("path", "")})
    cached = get_cache(key, ttl_seconds=cache_ttl)
    if cached is not None:
        print(f"[Fallback BOM] 缓存命中: {len(cached)} 个商品")
        return cached

    try:
        adapter = get_adapter(bom_config["adapter"])
        records = adapter.load(bom_config)

        boms = {}
        current_product = None

        for r in records:
            product_name = r.get("product_name")
            material_code = r.get("material_code")
            material_name = r.get("material_name")
            bom_num = r.get("qty")
            uom = r.get("unit")

            if product_name:
                current_product = str(product_name).strip()
                if current_product not in boms:
                    boms[current_product] = []

            if current_product and material_code and bom_num is not None:
                code_str = str(material_code).strip()
                if code_str in ("—", "-", "", "None", "null", "(无编号)"):
                    continue
                try:
                    num_val = float(bom_num)
                except (ValueError, TypeError):
                    continue
                existing_codes = {item[0] for item in boms[current_product]}
                if code_str in existing_codes:
                    continue
                uom_mapping = {"克": "g", "g": "g", "个": "pc", "pc": "pc", "份": "pc"}
                std_uom = uom_mapping.get(str(uom).strip(), str(uom).strip() if uom else "")
                boms[current_product].append((
                    code_str,
                    str(material_name or "").strip(),
                    num_val,
                    std_uom,
                ))

        # 对 fallback BOM 应用相同的替换/删除规则（统一口径）
        fb_drop = 0
        fb_replace = 0
        for product_name, recs in list(boms.items()):
            wrapped = [(c, n, num, u, 1.0, 0.0) for c, n, num, u in recs]
            new_wrapped = _apply_bom_overrides(wrapped)
            old_codes = {r[0] for r in recs}
            fb_drop += len(old_codes & BOM_DROP_CODES)
            fb_replace += len(old_codes & set(BOM_REPLACEMENTS.keys()))
            boms[product_name] = [(c, n, num, u) for c, n, num, u, _, _ in new_wrapped]
        if fb_drop or fb_replace:
            print(f"[Fallback BOM] 市场规则: 替换 {fb_replace} 处, 删除 {fb_drop} 处")

        set_cache(key, boms)
        print(f"[Fallback BOM] 加载 {len(boms)} 个商品的补充 BOM")
        return boms

    except Exception as e:
        print(f"[Fallback BOM] 加载失败: {e}")
        return {}


def _match_fallback_bom(item_name, fallback_boms):
    """用 BQ 商品名匹配 fallback BOM 中的商品名。

    优先级（从严到宽）：
      1. fallback key 整字符串与 item_name 精确相等
      2. key 的中文段（"/" 首段）与 item_name 精确相等
      3. 中文段以 item_name 开头（如 item="鸡块"，zh="鸡块（中）"）
      4. 中文段包含 item_name（如 item="鸡肉芝士球"，zh="周一特惠 - 鸡肉芝士球 2 盒 69"）
      5. 长前缀模糊（item ≥5 字符，前 10 字符出现在 zh 里）
    多个候选时取**中文段最短**的（最接近原始商品名）。

    历史 bug：旧逻辑遍历 dict 直接 `item_name in key` 会优先命中长 key，
    导致"鸡肉芝士球"被错误匹配到"周一特惠 - 鸡肉芝士球 2 盒 69"，
    成本按 2 盒装算（虚高一倍）。
    """
    if not item_name or not fallback_boms:
        return None
    name = item_name.strip()
    if not name:
        return None

    # 1. 整 key 精确
    if name in fallback_boms:
        return fallback_boms[name]

    # 预提取中文首段
    keys_zh = [(k, k.split(" / ")[0].strip()) for k in fallback_boms]

    # 2. 中文段精确
    for k, zh in keys_zh:
        if zh == name:
            return fallback_boms[k]

    # 3. 中文段以 item_name 开头
    starts = [(k, zh) for k, zh in keys_zh if zh.startswith(name) and zh != name]
    if starts:
        starts.sort(key=lambda x: len(x[1]))
        return fallback_boms[starts[0][0]]

    # 4. 中文段包含 item_name
    contains = [(k, zh) for k, zh in keys_zh if name in zh]
    if contains:
        contains.sort(key=lambda x: len(x[1]))
        return fallback_boms[contains[0][0]]

    # 5. 长前缀模糊
    if len(name) >= 5:
        prefix = name[:10]
        loose = [(k, zh) for k, zh in keys_zh if prefix in zh]
        if loose:
            loose.sort(key=lambda x: len(x[1]))
            return fallback_boms[loose[0][0]]

    return None


# ============================================================================
# 商家列表加载（适配器 + 缓存）
# ============================================================================

def _fetch_store_names_from_bq(uuids, project_id):
    """并发查每个 dataset 的 ttpos_setting 拿 store_code/store_name。"""
    from concurrent.futures import ThreadPoolExecutor
    from bq_reports.utils.bq_client import get_bq_client

    def _query(uuid_str):
        try:
            client = get_bq_client(project_id)
            sql = f"""
            SELECT
              JSON_EXTRACT_SCALAR(`values`, '$.store_code') AS code,
              JSON_EXTRACT_SCALAR(`values`, '$.store_name') AS name
            FROM `{project_id}`.`shop{uuid_str}`.`ttpos_setting`
            WHERE `key` = 'store' AND delete_time = 0
            LIMIT 1
            """
            rows = list(client.query(sql).result())
            if not rows:
                return uuid_str, None, None
            r = rows[0]
            return uuid_str, (r.code or None), (r.name or None)
        except Exception as e:
            print(f"[警告] 查询 shop{uuid_str} 的 store_name 失败: {e}")
            return uuid_str, None, None

    result = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for uuid_str, code, name in ex.map(_query, uuids):
            result[uuid_str] = (code, name)
    return result


def _load_merchants(config: dict, store_names: dict, override_path: str = None,
                    project_id: str = None):
    """加载商家列表。门店名优先从 BQ ttpos_setting 实时取，
    fallback 到 store_names Excel 映射，再 fallback 到 "-"。
    门店编号同样优先 BQ store_code，否则用 admin-XXX 解析出的数字。"""
    merchant_cfg = config.get("merchant_list")
    if merchant_cfg:
        cache_ttl = config.get("cache", {}).get("merchant_list_ttl", 86400)
        # v3: 接入 BQ 实时门店名
        key = cache_key("merchants_v3", {"path": merchant_cfg.get("path", ""),
                                          "project": project_id or ""})
        cached = get_cache(key, ttl_seconds=cache_ttl)
        if cached is not None:
            print(f"[Merchants] 缓存命中: {len(cached)} 个")
            return cached

        adapter = get_adapter(merchant_cfg["adapter"])
        records = adapter.load(merchant_cfg)
        raw = []
        for r in records:
            account = r.get("account")
            uuid_str = r.get("uuid")
            if not account or not uuid_str:
                continue
            account = str(account).strip()
            uuid_str = str(uuid_str).strip()
            m = re.search(r'admin-(\d+)@', account)
            store_num_excel = m.group(1) if m else account
            raw.append((account, uuid_str, store_num_excel))

        # 从 BQ 拉真实门店名（覆盖 Excel 映射）
        bq_names = {}
        if project_id and raw:
            print(f"[Merchants] 从 BQ 查询 {len(raw)} 个门店的 store_code/store_name...")
            bq_names = _fetch_store_names_from_bq([r[1] for r in raw], project_id)

        merchants = []
        for account, uuid_str, store_num_excel in raw:
            bq_code, bq_name = bq_names.get(uuid_str, (None, None))
            store_num = bq_code or store_num_excel
            store_name = bq_name
            if not store_name:
                store_name = store_names.get(store_num_excel)
                if not store_name and store_num_excel.isdigit():
                    store_name = store_names.get(str(int(store_num_excel)))
            store_name = store_name or "-"
            merchants.append((account, uuid_str, store_num, store_name))
        set_cache(key, merchants)
        return merchants
    else:
        # 回退：直接读 Excel（兼容旧用法）
        from openpyxl import load_workbook
        path = override_path or "resources/merchants.xlsx"
        wb = load_workbook(path, data_only=True)
        ws = wb.active
        merchants = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if len(row) >= 3 and row[1] and row[2]:
                account = str(row[1]).strip()
                uuid_str = str(row[2]).strip()
                m = re.search(r'admin-(\d+)@', account)
                store_num = m.group(1) if m else account
                store_name = store_names.get(store_num, "-")
                merchants.append((account, uuid_str, store_num, store_name))
        wb.close()
        return merchants


# ============================================================================
# SQL 模板（聚合优化版）
# ============================================================================

# 套餐 / 单品订单聚合 — 按 ttpos `CountProductSale` 算法（statistics_product + takeout_order_item）
#
# ttpos 源码: ttpos-server-go/main/app/repository/statistics.go:1933-2143 (CountProductSale)
#
# Shop (statistics_product):
#   sale_num   = SUM(product_num)
#   sale_amount(actual) = SUM(IF(free_num>0 OR give_num>0, 0,
#                  product_final_price * (product_num - refund_num)))
# Takeout (takeout_order_item + takeout_order):
#   sale_num   = SUM(quantity)
#   sale_amount(actual) = SUM(IF(order_state=60, 0, price * quantity))
#   order_state IN (10,20,30,40,60), accepted_time > 0
# 合并：按 product_package_uuid FULL OUTER JOIN
#
# 注意:
#   1. ttpos 不展开 套餐子商品（无 copy_num × unit_num 乘子）— 跟我们之前实现差异大
#   2. cancelled (state=60) 订单 sale_num 计入但 amount 计 0（ttpos 设计，争议）
#   3. 赠品 (free_num/give_num) amount 直接归零
#   4. shop 端退款通过 (product_num - refund_num) 反映
#   5. std_unit_price 保留我们的算法（按销量加权 product_bom.price），ttpos 没有这个概念

_PROFIT_SALES_TPL = """
WITH
shop_sales AS (
  -- ttpos 源码: ttpos-server-go/main/app/repository/statistics.go:1980-2046 (CountProductSale - ExportProductSales 接口真实算法)
  --   GET /statistics/product_sales/export 路由 → service/business.go:845 ExportProductSales
  -- 注意: 不能用 RankProduct (statistics.go:1245) 的 refund_time=0 过滤 —— 那是 top10 排行，跟导出算法不一样
  --   actual_sale_amount = SUM(IF(free|give, 0, final_price * (num - refund_num)))
  --   时间字段: buildCountOpts 默认走 complete_time
  SELECT
    sp.product_package_uuid AS item_uuid,
    SUM(sp.product_num) AS qty,
    SUM(IF(sp.free_num > 0 OR sp.give_num > 0, 0,
           sp.product_final_price * (sp.product_num - sp.refund_num))) AS revenue
  FROM `{project}`.`{dataset}`.`ttpos_statistics_product` sp
  WHERE sp.complete_time >= {start_ts}
    AND sp.complete_time < {end_ts}
  GROUP BY item_uuid
),
takeout_sales AS (
  -- ttpos 源码: ttpos-server-go/main/app/repository/statistics_takeout.go:451-502 (RankTakeoutProduct)
  -- 时间过滤是 dynamic time condition: state=40 用 completed_time, 其他用 accepted_time
  -- 营业额只算 state IN (10,20,30,40)，state=60 取消订单计 0
  SELECT
    toi.ttpos_product_package_uuid AS item_uuid,
    SUM(toi.quantity) AS qty,
    SUM(IF(t.order_state IN (10,20,30,40), toi.price * toi.quantity, 0)) AS revenue
  FROM `{project}`.`{dataset}`.`ttpos_takeout_order_item` toi
  JOIN `{project}`.`{dataset}`.`ttpos_takeout_order` t
    ON t.uuid = toi.takeout_order_uuid AND t.delete_time = 0
  WHERE toi.delete_time = 0
    AND toi.ttpos_product_package_uuid > 0
    AND t.order_state IN (10, 20, 30, 40, 60)
    AND t.accepted_time > 0
    AND (
      (t.order_state = 40 AND t.completed_time >= {start_ts} AND t.completed_time < {end_ts})
      OR (t.order_state != 40 AND t.accepted_time >= {start_ts} AND t.accepted_time < {end_ts})
    )
  GROUP BY item_uuid
),
merged AS (
  SELECT
    COALESCE(s.item_uuid, t.item_uuid) AS item_uuid,
    IFNULL(s.qty, 0) + IFNULL(t.qty, 0) AS qty,
    IFNULL(s.revenue, 0) + IFNULL(t.revenue, 0) AS revenue
  FROM shop_sales s
  FULL OUTER JOIN takeout_sales t USING (item_uuid)
)
SELECT
  m.item_uuid,
  -- 部分商品名末尾带回车/换行/制表符等不可见字符（前端 trim 显示无异，但 BQ 取出来会带）
  -- 用 REGEXP_REPLACE 去掉首尾不可见字符，避免渲染成 _x000D_ 看似两个不同商品
  REGEXP_REPLACE(COALESCE(
    JSON_EXTRACT_SCALAR(pp.name, '$.zh'),
    JSON_EXTRACT_SCALAR(pp.name, '$.en'),
    '未知'
  ), r'^\\s+|\\s+$', '') AS item_name,
  m.qty AS qty,
  m.revenue AS revenue,
  -- 单价取 Shop 商品管理标价 ttpos_product_package.price
  IFNULL(pp.price, 0) AS list_price
FROM merged m
-- ttpos 导出用 LEFT JOIN，不过滤 pp.delete_time（已删除的商品也算销售）
-- 但本报表必须按 product_type 区分套餐/单品 sheet，所以仍 INNER JOIN，去掉 delete_time 过滤
JOIN `{project}`.`{dataset}`.`ttpos_product_package` pp
  ON pp.uuid = m.item_uuid
WHERE pp.product_type = {product_type}
  AND m.qty > 0
"""

COMBO_ORDERS_SQL = _PROFIT_SALES_TPL.replace("{product_type}", "1")
SINGLE_ORDERS_SQL = _PROFIT_SALES_TPL.replace("{product_type}", "0")

# 产品 BOM 结构（不关联订单表，纯产品级数据）
# bom_unit / conversion_rate 来自 ttpos 配方录入，用于把 ERP 基准单位价换算到配方单位价
#
# 软删除处理: ttpos 商品/规格被软删后，pb.delete_time != 0，但历史销售 (statistics_product)
# 仍引用该 product_package_uuid（例: 蜜汁手扒半鸡 4-22 软删，3 月销售仍存在）。
# 直接 WHERE pb.delete_time = 0 会让这类商品的 BOM 全丢、被迫走 fallback Excel → 算错。
# 用 window 函数实现"active 优先 + 没 active 才回退 deleted"：
#   - 同一 product_package_uuid 下若存在 active 行 (delete_time=0)，只取 active
#   - 全部已被软删时，把 deleted 行也带回来（用于已下架但有历史销售的商品）
BOM_SQL = """
WITH bom_with_flag AS (
  SELECT
    pb.uuid,
    pb.product_package_uuid,
    pb.product_bom_card_uuid,
    pb.delete_time,
    SUM(CASE WHEN pb.delete_time = 0 THEN 1 ELSE 0 END)
      OVER (PARTITION BY pb.product_package_uuid) AS active_count
  FROM `{project}`.`{dataset}`.`ttpos_product_bom` pb
)
SELECT
  pb.product_package_uuid AS item_uuid,
  m.code AS material_code,
  JSON_EXTRACT_SCALAR(m.name, '$.zh') AS material_name,
  rm.num AS bom_num,
  COALESCE(
    JSON_EXTRACT_SCALAR(rm.unit_name, '$.zh'),
    JSON_EXTRACT_SCALAR(rm.unit_name, '$.en'),
    JSON_EXTRACT_SCALAR(rm.base_unit_name, '$.zh'),
    JSON_EXTRACT_SCALAR(rm.base_unit_name, '$.en')
  ) AS bom_unit,
  rm.base_unit_conversion_rate AS conversion_rate,
  m.price AS material_bq_price
FROM bom_with_flag pb
LEFT JOIN `{project}`.`{dataset}`.`ttpos_related_material` rm
  ON (
    (pb.product_bom_card_uuid > 0 AND rm.related_uuid = pb.product_bom_card_uuid)
    OR (pb.product_bom_card_uuid = 0 AND rm.related_uuid = pb.uuid)
  )
  AND rm.delete_time = 0
LEFT JOIN `{project}`.`{dataset}`.`ttpos_material` m
  ON m.uuid = rm.material_uuid AND m.delete_time = 0
WHERE (pb.delete_time = 0 OR pb.active_count = 0)
"""

# 套餐结构（combo_uuid -> child_uuid），从当前时间范围订单推断
COMBO_STRUCTURE_SQL = """
SELECT DISTINCT
  parent_sop.product_package_uuid AS combo_uuid,
  child_sop.product_package_uuid AS child_uuid
FROM `{project}`.`{dataset}`.`ttpos_sale_order_product` parent_sop
JOIN `{project}`.`{dataset}`.`ttpos_sale_order_product` child_sop
  ON child_sop.package_uuid = parent_sop.uuid
  AND child_sop.delete_time = 0
JOIN `{project}`.`{dataset}`.`ttpos_sale_bill` sb
  ON sb.uuid = parent_sop.sale_bill_uuid AND sb.delete_time = 0
WHERE parent_sop.product_type = 1
  AND parent_sop.delete_time = 0
  AND sb.status = 1
  AND sb.finish_time >= {start_ts}
  AND sb.finish_time < {end_ts}
"""


# ============================================================================
# 价格解析
# ============================================================================

BOM_UNIT_CORRECTIONS = {
    "MK01018": 50,
}

# 市场要求的 BOM 物料替换/删除规则（套餐和单品都生效）
# value 为 None 时保留旧 material_name；指定字符串时覆盖名称。
BOM_REPLACEMENTS = {
    "FR01008": ("FR02001", None),
    "VE01001": ("MK01018", None),
}
BOM_DROP_CODES = {"TL99008"}


def _apply_bom_overrides(bom_records):
    """对单个 (store, item) 的 BOM 列表应用替换/删除规则。

    record 元组顺序: (material_code, material_name, bom_num, bom_unit, conv_rate, bq_price)

    两轮合并：先收录所有未被替换的物料（元数据权威），再把被替换的并入 —
    若目标 code 已存在则只累加 bom_num，保留真实物料的 name/unit/conv/price。
    """
    merged = {}
    deferred = []
    for code, name, bom_num, bom_unit, conv_rate, bq_price in bom_records:
        if code in BOM_DROP_CODES:
            continue
        if code in BOM_REPLACEMENTS:
            new_code, new_name = BOM_REPLACEMENTS[code]
            override_name = new_name if new_name is not None else name
            deferred.append((new_code, override_name, bom_num, bom_unit, conv_rate, bq_price))
            continue
        if code in merged:
            prev_name, prev_num, prev_unit, prev_conv, prev_price = merged[code]
            merged[code] = (
                prev_name or name,
                prev_num + bom_num,
                prev_unit or bom_unit,
                prev_conv,
                prev_price,
            )
        else:
            merged[code] = (name, bom_num, bom_unit, conv_rate, bq_price)

    for code, name, bom_num, bom_unit, conv_rate, bq_price in deferred:
        if code in merged:
            prev_name, prev_num, prev_unit, prev_conv, prev_price = merged[code]
            merged[code] = (prev_name, prev_num + bom_num, prev_unit, prev_conv, prev_price)
        else:
            merged[code] = (name, bom_num, bom_unit, conv_rate, bq_price)

    return [(c, n, bn, bu, cr, bp) for c, (n, bn, bu, cr, bp) in merged.items()]


def _resolve_base_unit_price(material_code, bq_price, erp_prices):
    """返回物料在基准单位下的单价（ERP Item Price 优先，否则用 BQ 缺省价）。"""
    if not erp_prices or not material_code:
        return float(bq_price or 0)
    for key in (material_code, material_code.upper(), material_code.lower()):
        if key in erp_prices:
            price, _uom = erp_prices[key]
            for corr_key in (material_code, material_code.upper(), material_code.lower()):
                if corr_key in BOM_UNIT_CORRECTIONS:
                    price = price / BOM_UNIT_CORRECTIONS[corr_key]
                    break
            return price
    return float(bq_price or 0)


# ============================================================================
# 套餐结构加载（缓存）
# ============================================================================

def _load_combo_structures(engine, merchants, start_ts, end_ts, config: dict = None):
    """
    查询并缓存每个门店的套餐结构。
    返回: {store_num: {combo_uuid: [child_uuid, ...]}}
    """
    cfg = config or {}
    cache_ttl = cfg.get("cache", {}).get("combo_structure_ttl", 604800)  # 7天
    key = cache_key("combo_structures", {"count": len(merchants), "start": start_ts, "end": end_ts})
    cached = get_cache(key, ttl_seconds=cache_ttl)
    if cached is not None:
        print(f"[Combo Structure] 缓存命中: {len(cached)} 个门店")
        return cached

    print("[Combo Structure] 从 BQ 查询套餐结构...")
    raw_rows, errors = engine.query(
        sql_template=COMBO_STRUCTURE_SQL,
        merchants=merchants,
        start_ts=start_ts,
        end_ts=end_ts,
        workers=10,
        row_proxy_factory=lambda row, acc, num, name: type("RowProxy", (), {
            "__getattr__": lambda self, attr: getattr(row, attr),
            "account": acc, "store_num": num, "store_name": name,
        })(),
        label="套餐结构",
    )

    structures = {}
    for row in raw_rows:
        store_num = row.store_num
        if store_num not in structures:
            structures[store_num] = {}
        combo_uuid = str(row.combo_uuid)
        child_uuid = str(row.child_uuid)
        if combo_uuid not in structures[store_num]:
            structures[store_num][combo_uuid] = []
        if child_uuid not in structures[store_num][combo_uuid]:
            structures[store_num][combo_uuid].append(child_uuid)

    set_cache(key, structures)
    total_combos = sum(len(v) for v in structures.values())
    print(f"[Combo Structure] 加载 {len(structures)} 个门店，共 {total_combos} 个套餐")
    return structures


# ============================================================================
# BOM 加载（缓存）
# ============================================================================

def _load_boms(engine, merchants, config: dict = None):
    """
    查询并缓存每个门店的产品 BOM。
    返回: {store_num: {item_uuid: [(material_code, material_name, bom_num, bom_unit, conv_rate, bq_price), ...]}}
    """
    cfg = config or {}
    cache_ttl = cfg.get("cache", {}).get("bom_ttl", 86400)  # 1天
    # v2: 增加 bom_unit / conversion_rate 字段，旧缓存格式不兼容
    # v3: BOM 加载层去重 (store, item, material)，旧缓存有重复行
    # v4: 应用市场 BOM 替换/删除规则 (FR01008→FR02001, VE01001→MK01018, drop TL99008)
    # v5: 软删商品的 BOM fallback —— pb.delete_time != 0 但全店无 active 时仍纳入
    key = cache_key("boms_v5", {"count": len(merchants)})
    cached = get_cache(key, ttl_seconds=cache_ttl)
    if cached is not None:
        print(f"[BOM] 缓存命中: {len(cached)} 个门店")
        return cached

    print("[BOM] 从 BQ 查询产品 BOM...")
    raw_rows, errors = engine.query(
        sql_template=BOM_SQL,
        merchants=merchants,
        start_ts=0,
        end_ts=2147483647,
        workers=10,
        row_proxy_factory=lambda row, acc, num, name: type("RowProxy", (), {
            "__getattr__": lambda self, attr: getattr(row, attr),
            "account": acc, "store_num": num, "store_name": name,
        })(),
        label="BOM",
    )

    boms = {}
    # ttpos 多规格商品的 product_bom × related_material JOIN 会让同一 material
    # 在同 (store, item) 内重复出现 N 次。在加载层去重，让套餐子商品共用物料的
    # 跨 child 累加逻辑安全（不会被 intra-item 冗余污染）。
    seen_keys = {}
    for row in raw_rows:
        store_num = row.store_num
        item_uuid = str(row.item_uuid)
        material_code = row.material_code
        if not material_code:
            continue
        dedup_key = (store_num, item_uuid, str(material_code))
        if dedup_key in seen_keys:
            continue
        seen_keys[dedup_key] = True
        if store_num not in boms:
            boms[store_num] = {}
        if item_uuid not in boms[store_num]:
            boms[store_num][item_uuid] = []
        boms[store_num][item_uuid].append((
            str(material_code),
            row.material_name or "",
            float(row.bom_num or 0),
            (row.bom_unit or "").strip() or "-",
            float(row.conversion_rate or 1),
            float(row.material_bq_price or 0),
        ))

    # 应用市场 BOM 替换/删除规则
    drop_count = 0
    replace_count = 0
    for store_num, items in boms.items():
        for item_uuid, records in list(items.items()):
            new_records = _apply_bom_overrides(records)
            old_codes = {r[0] for r in records}
            new_codes = {r[0] for r in new_records}
            drop_count += len(old_codes & BOM_DROP_CODES)
            replace_count += len(old_codes & set(BOM_REPLACEMENTS.keys()))
            items[item_uuid] = new_records
    if drop_count or replace_count:
        print(f"[BOM] 市场规则: 替换 {replace_count} 处, 删除 {drop_count} 处")

    set_cache(key, boms)
    total_items = sum(len(v) for v in boms.values())
    print(f"[BOM] 加载 {len(boms)} 个门店，共 {total_items} 个产品的 BOM")
    return boms


# ============================================================================
# 数据聚合（新版：预聚合 orders + 预加载 BOM）
# ============================================================================

def aggregate_with_bom(order_rows, bom_data, combo_structure, erp_prices=None, mode="combo"):
    """
    聚合订单和 BOM 数据。

    Args:
        order_rows: 引擎返回的订单行（已带 store_num, store_name）
        bom_data: {store_num: {item_uuid: [(material_code, ...), ...]}}
        combo_structure: {store_num: {combo_uuid: [child_uuid, ...]}}
        mode: "combo" 或 "single"
    """
    data = {}
    for row in order_rows:
        store_num = row.store_num
        store_name = row.store_name
        item_uuid = str(row.item_uuid)
        item_name = row.item_name
        qty = float(row.qty or 0)
        revenue = float(row.revenue or 0)
        list_price = float(getattr(row, "list_price", None) or 0)

        key = (store_num, store_name, item_uuid, item_name)
        if key not in data:
            data[key] = {
                "qty": 0.0,
                "revenue": 0.0,
                "list_price": list_price,  # ttpos_product_package.price，每个 item 唯一
                "bom": {},
            }
        data[key]["qty"] += qty
        data[key]["revenue"] += revenue

    # 为每个 item 匹配 BOM
    for key, val in data.items():
        store_num, store_name, item_uuid, item_name = key
        store_boms = bom_data.get(store_num, {})

        if mode == "combo":
            # 套餐：合并所有子产品的 BOM。同一物料被多个子商品共用时累加 num/cost。
            store_struct = combo_structure.get(store_num, {})
            child_uuids = store_struct.get(item_uuid, [])
            for child_uuid in child_uuids:
                child_bom = store_boms.get(child_uuid, [])
                for material_code, material_name, bom_num, bom_unit, conv_rate, bq_price in child_bom:
                    if not material_code:
                        continue
                    base_price = _resolve_base_unit_price(material_code, bq_price, erp_prices)
                    unit_price = base_price * (conv_rate or 1)
                    delta_cost = bom_num * unit_price
                    if material_code in val["bom"]:
                        prev_name, prev_num, prev_up, prev_unit, prev_cost = val["bom"][material_code]
                        val["bom"][material_code] = (
                            prev_name or material_name,
                            prev_num + bom_num,
                            unit_price,                  # 同物料同单位价不变
                            prev_unit or bom_unit,
                            prev_cost + delta_cost,
                        )
                    else:
                        val["bom"][material_code] = (material_name, bom_num, unit_price, bom_unit, delta_cost)
        else:
            # 单品：直接匹配 BOM。同一商品多个 product_bom（不同 flavor/sauce 但共用 bom_card）
            # 会重复返回相同 material_code，按 dedup 处理（避免 N 倍虚增）。
            item_bom = store_boms.get(item_uuid, [])
            for material_code, material_name, bom_num, bom_unit, conv_rate, bq_price in item_bom:
                if not material_code:
                    continue
                if material_code not in val["bom"]:
                    base_price = _resolve_base_unit_price(material_code, bq_price, erp_prices)
                    unit_price = base_price * (conv_rate or 1)
                    cost_per_unit = bom_num * unit_price
                    val["bom"][material_code] = (material_name, bom_num, unit_price, bom_unit, cost_per_unit)

    # 转换为列表格式（与旧版兼容）
    result = {}
    for key, val in data.items():
        result[key] = {
            "qty": val["qty"],
            "revenue": val["revenue"],
            "list_price": val["list_price"],
            "bom": [
                (code, name, bom_num, price, uom, cost)
                for code, (name, bom_num, price, uom, cost) in val["bom"].items()
            ]
        }
    return result


# ============================================================================
# 扁平化行构建
# ============================================================================

def _build_rows(agg_data, mode, fallback_boms=None, erp_prices=None):
    """
    将聚合数据扁平化为引擎可消费的 list[list]。
    每行 15 个展示列 + 第 16 列隐藏 item_uuid（用作 merge_key，避免同名不同 uuid
    被错合并 —— 例如「合艾炸鸡（中翅 8块）」¥79/¥99 两个 SKU 共名，
    若不带 uuid 区分会被 ReportEngine block 检测合并掉，第二个 SKU 销量被吞）。
    """
    rows = []
    for (store_num, store_name, item_uuid, item_name), data in sorted(agg_data.items()):
        qty = data["qty"]
        revenue = data["revenue"]
        # 单价取 Shop 商品管理标价 ttpos_product_package.price
        item_unit_price = round(data.get("list_price", 0) or 0, 2)
        bom_list = data["bom"]

        # Fallback BOM 补充（fallback 数据按基准单位录入，conv_rate 视为 1）
        if not bom_list and fallback_boms:
            matched = _match_fallback_bom(item_name, fallback_boms)
            if matched:
                bom_list = []
                for code, name, bom_num, uom in matched:
                    unit_price = _resolve_base_unit_price(code, 0, erp_prices)
                    cost_per_unit = bom_num * unit_price
                    bom_list.append((code, name, bom_num, unit_price, uom or "-", cost_per_unit))
                print(f"  [Fallback] {item_name}: 补充 {len(bom_list)} 个物料")

        if not bom_list:
            rows.append([
                store_num, store_name, item_name,
                round(qty, 2), item_unit_price, round(revenue, 2),
                "-", "-", 0, 0, "-", 0,
                0,                          # 12 单份总成本
                round(item_unit_price, 2),  # 13 单品毛利 = 单价 - 0
                round(revenue, 2),          # 14 总毛利 = 销售额 - 0
                1.0 if revenue > 0 else 0,  # 15 毛利率
                str(item_uuid),             # 16 隐藏 merge_key
            ])
            continue

        per_unit_bom_cost = sum(cost for _, _, _, _, _, cost in bom_list)
        unit_profit = item_unit_price - per_unit_bom_cost   # 单品毛利
        # 总毛利按 market 要求 = 销量 × 单品毛利（不再用 F - M*D）
        gross_profit = qty * unit_profit
        gross_margin = gross_profit / revenue if revenue > 0 else 0

        for code, name, bom_num, mat_price, uom, cost in bom_list:
            rows.append([
                store_num, store_name, item_name,
                round(qty, 2), item_unit_price, round(revenue, 2),
                name, code, round(mat_price, 4), round(bom_num, 4), uom or "-", round(cost * qty, 2),
                round(per_unit_bom_cost, 2),
                round(unit_profit, 2),         # 13 单品毛利
                round(gross_profit, 2),        # 14 总毛利（原毛利）
                round(gross_margin, 4),        # 15 毛利率
                str(item_uuid),                # 16 隐藏 merge_key
            ])

    return rows


# ============================================================================
# 资源配置加载
# ============================================================================

def load_config(config_path: str = None) -> dict:
    """加载资源配置 YAML。"""
    import yaml
    path = config_path or os.path.join(
        os.path.dirname(__file__), "..",
        "resources", "wallace.20260422", "config.yaml"
    )
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ============================================================================
# 主流程
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="利润报表导出")
    parser.add_argument("--mode", default="both", choices=["combo", "single", "both"], help="报表模式")
    parser.add_argument("--month", default=None, help="月份，格式 YYYY-MM（与 --start-date/--end-date 互斥）")
    parser.add_argument("--start-date", default=None, help="开始日期，格式 YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="结束日期，格式 YYYY-MM-DD")
    parser.add_argument("--merchants", default="resources/merchants.xlsx", help="商家列表 Excel 路径")
    parser.add_argument("--output", default=None, help="输出 Excel 文件路径（默认自动推导）")
    parser.add_argument("--project", default="diyl-407103", help="GCP 项目 ID")
    parser.add_argument("--use-erp-price", action="store_true", default=True, help="启用 ERPNext Item Price 替换 BQ 成本（默认开启）")
    parser.add_argument("--no-erp-price", action="store_true", help="禁用 ERPNext 价格，使用 BQ 内置价格")
    parser.add_argument("--erp-price-list", default=None, help="ERPNext 价格表名称，默认 Standard Buying")
    parser.add_argument("--config", default=None, help="资源配置 YAML 路径")
    parser.add_argument("--column-config", default="resources/reports/profit_margin.yaml", help="列配置 YAML 路径")
    parser.add_argument("--no-cache", action="store_true", help="禁用缓存")
    args = parser.parse_args()

    # 时间范围解析
    if args.month and (args.start_date or args.end_date):
        print("[错误] --month 与 --start-date/--end-date 不能同时使用")
        return 1

    if args.month:
        year, mon = int(args.month[:4]), int(args.month[5:7])
        start_dt = datetime(year, mon, 1, tzinfo=BKK_TZ)
        if mon == 12:
            end_dt = datetime(year + 1, 1, 1, tzinfo=BKK_TZ)
        else:
            end_dt = datetime(year, mon + 1, 1, tzinfo=BKK_TZ)
        range_label = args.month.replace("-", "")
    elif args.start_date and args.end_date:
        start_dt = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=BKK_TZ)
        end_dt = datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=BKK_TZ) + timedelta(days=1)
        range_label = f"{args.start_date.replace('-', '')}_{args.end_date.replace('-', '')}"
    else:
        print("[错误] 必须指定 --month 或 --start-date + --end-date")
        return 1

    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    # 自动推导输出路径
    output_path = args.output or f"exports/profit_{range_label}.xlsx"

    # 初始化引擎
    engine = ReportEngine(project_id=args.project)

    # 加载资源配置
    config = load_config(args.config)

    # 加载 ERPNext 价格（带缓存）
    erp_prices = {}
    if args.use_erp_price and not args.no_erp_price:
        erp_ttl = 0 if args.no_cache else config.get("cache", {}).get("erp_prices_ttl", 3600)
        erp_prices = _try_load_erp_prices(price_list=args.erp_price_list, cache_ttl=erp_ttl)
        if not erp_prices:
            print("[警告] 未加载到 ERPNext 价格，成本将显示为 0")
        print()

    # 加载门店名称映射
    store_names = _load_store_names(config)

    # 加载商家列表
    merchants = _load_merchants(config, store_names, override_path=args.merchants,
                                  project_id=args.project)

    range_desc = args.month if args.month else f"{args.start_date} ~ {args.end_date}"
    print(f"模式: {args.mode}, 时间: {range_desc}")
    print(f"时间范围: {start_ts} - {end_ts}")
    print(f"门店数: {len(merchants)}")
    print()

    # 加载补充 BOM
    fallback_boms = _load_fallback_boms(config)

    # 预加载套餐结构（如果 mode 包含 combo）
    combo_structure = {}
    if args.mode in ("combo", "both"):
        combo_structure = _load_combo_structures(engine, merchants, start_ts, end_ts, config)
        print()

    # 预加载 BOM（所有 mode 都需要）
    bom_data = _load_boms(engine, merchants, config)
    print()

    # 确定要处理的 mode
    modes = []
    if args.mode in ("combo", "both"):
        modes.append("combo")
    if args.mode in ("single", "both"):
        modes.append("single")

    # 准备输出
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    import xlsxwriter
    wb = xlsxwriter.Workbook(str(output_path))

    for mode in modes:
        item_label = "套餐" if mode == "combo" else "单品"
        sql_template = COMBO_ORDERS_SQL if mode == "combo" else SINGLE_ORDERS_SQL
        print(f"\n========== 开始处理 {item_label} ==========\n")

        # 并发查询聚合后的订单
        raw_rows, errors = engine.query(
            sql_template=sql_template,
            merchants=merchants,
            start_ts=start_ts,
            end_ts=end_ts,
            workers=10,
            row_proxy_factory=lambda row, acc, num, name: type("RowProxy", (), {
                "__getattr__": lambda self, attr: getattr(row, attr),
                "account": acc, "store_num": num, "store_name": name,
            })(),
            label=item_label,
        )

        # 聚合（订单 + BOM）
        agg_data = aggregate_with_bom(
            raw_rows, bom_data, combo_structure,
            erp_prices=erp_prices, mode=mode
        )

        # 扁平化
        flat_rows = _build_rows(agg_data, mode, fallback_boms=fallback_boms, erp_prices=erp_prices)

        # 加载列配置并写入 Excel
        sheet_cfg = engine.load_sheet_config(args.column_config, item_label)
        engine.write_sheet(wb, item_label, sheet_cfg, flat_rows)

        # field 13 = 单品毛利, field 14 = 总毛利
        neg_unit = sum(1 for r in flat_rows if r[13] is not None and r[13] < 0)
        neg_total = sum(1 for r in flat_rows if r[14] is not None and r[14] < 0)
        print(f"\n[{item_label}] 总明细行数: {len(flat_rows)} 行")
        if neg_unit > 0:
            print(f"[{item_label}] 单品毛利<0: {neg_unit} 行（标价 < 单份成本，赔本商品）")
        if neg_total > 0:
            print(f"[{item_label}] 总毛利<0: {neg_total} 行（实收 < 总成本，含折扣损失）")

    wb.close()
    print(f"\n输出文件: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
