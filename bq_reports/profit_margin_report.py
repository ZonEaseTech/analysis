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

# 跟 ttpos 业务时区对齐（曼谷 +07:00），月份边界以 BKK 时间为准。
# 真源在 semantic/dimensions/time.py；这里 re-export 保持现有 tests 和
# 报表脚本 from-import 兼容。
from semantic.dimensions.time import BKK_TZ  # noqa: E402
from pathlib import Path

from bq_reports.utils.bq_client import setup_proxy
from semantic.dimensions.time import month_to_ts_range as _month_to_ts_range
from semantic.entities import bom, combo, price_breakdown, sale_line, takeout_line, total_line
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

def _load_uploaded_prices(excel_path: str) -> tuple[dict, dict]:
    """从上传的 Excel 价格清单读取物料单价和换算系数。
    支持 '干冻货' 和 '设备材料' 两个 sheet，以及 '盘点单位匹配分析' sheet。
    返回: (prices: {material_code: unit_price}, conversions: {material_code: conv_rate})
    其中 unit_price = 清单单价 ÷ 销售换算系数（得到最小单位单价）
    """
    if not excel_path or not os.path.exists(excel_path):
        return {}, {}
    try:
        import openpyxl
        wb = openpyxl.load_workbook(excel_path, data_only=True)
        
        # 1) 读取销售换算系数（从"盘点单位匹配分析"）
        conversions = {}
        if "盘点单位匹配分析" in wb.sheetnames:
            ws_conv = wb["盘点单位匹配分析"]
            for row in ws_conv.iter_rows(min_row=2, values_only=True):
                code = row[0]
                conv = row[8]  # 销售换算系数 (I列)
                if code and conv is not None:
                    try:
                        conv_val = float(conv)
                        if conv_val > 0:
                            conversions[str(code).strip()] = conv_val
                    except (ValueError, TypeError):
                        pass
            print(f"[Uploaded Prices] 加载 {len(conversions)} 条换算系数")
        
        # 2) 读取单价并换算
        prices = {}
        for sheet_name in ("干冻货", "设备材料"):
            if sheet_name not in wb.sheetnames:
                continue
            ws = wb[sheet_name]
            for row in ws.iter_rows(min_row=2, values_only=True):
                code = row[0]
                price = row[7] if len(row) > 7 else None
                if code and price is not None and price != '#N/A':
                    try:
                        price_val = float(price)
                        code_str = str(code).strip()
                        # 用最小单位单价 = 盘点单价 ÷ 销售换算系数
                        conv = conversions.get(code_str, 1)
                        prices[code_str] = price_val / conv
                    except (ValueError, TypeError):
                        pass
        print(f"[Uploaded Prices] 从 {excel_path} 加载 {len(prices)} 条最小单位单价")
        return prices, conversions
    except Exception as e:
        print(f"[警告] 加载上传价格清单失败: {e}")
        return {}, {}


def _try_load_erp_prices(price_list: str = None, cache_ttl: int = 3600):
    from bq_reports.utils.erpnext_api import load_erpnext_prices
    key = cache_key("erpnext_prices", {"price_list": price_list or "Standard Buying"})
    
    # 1) 先尝试缓存（正常 TTL）
    cached = get_cache(key, ttl_seconds=cache_ttl)
    if cached is not None:
        print(f"[ERPNext API] 缓存命中: {len(cached)} 条价格")
        return cached
    
    # 2) 缓存未命中，尝试 API
    try:
        prices = load_erpnext_prices(price_list=price_list)
        set_cache(key, prices)
        return prices
    except Exception as e:
        print(f"[警告] ERPNext API 失败: {e}")
    
    # 3) API 也失败，强制读缓存（忽略 TTL，永不过期 fallback）
    cached = get_cache(key, ttl_seconds=99999999)
    if cached is not None:
        print(f"[ERPNext API] API 不可用，使用过期缓存: {len(cached)} 条价格")
        return cached
    
    print("[警告] 未加载到 ERPNext 价格，成本将显示为 0")
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

# _PROFIT_SALES_TPL is assembled from the semantic layer's CTE factories. The
# entity strings still carry literal `{project}` / `{dataset}` / `{start_ts}` /
# `{end_ts}` placeholders (they pass through f-string interpolation unchanged
# because the outer f-string only resolves its own `{…}` expressions). The final
# `{{product_type}}` is escaped to a literal `{product_type}` so engine.query()
# can still substitute it per-call via `.replace()` below.
_PROFIT_SALES_TPL = f"""
WITH
-- 价格拆分：取前3个主要价格档（按销量降序），其余归到"其他"
-- 必须同时覆盖堂食 + 外卖，否则销量/营业额拆分对不上下游 shop_sales+takeout_sales 合并值
{price_breakdown.price_top3_ctes()},
{sale_line.shop_sales_cte()},
{takeout_line.takeout_sales_cte()},
{total_line.merged_cte()}
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
  m.sales_price AS sales_price,
  m.original_amount AS original_amount,
  m.avg_member_discount AS avg_member_discount,
  m.free_qty AS free_qty,
  m.give_qty AS give_qty,
  m.refund_qty AS refund_qty,
  m.refund_amount AS refund_amount,
  m.cancelled_qty AS cancelled_qty,
  m.cancelled_amount AS cancelled_amount,
  -- 价格拆分：前3个主要价格档 + 其他
  p3.price_1 AS price_1,
  p3.qty_1 AS qty_1,
  p3.price_2 AS price_2,
  p3.qty_2 AS qty_2,
  p3.price_3 AS price_3,
  p3.qty_3 AS qty_3,
  p3.other_qty AS other_price_qty,
  -- 单价取 Shop 商品管理标价 ttpos_product_package.price
  IFNULL(pp.price, 0) AS list_price
FROM merged m
-- 价格拆分 JOIN
LEFT JOIN price_top3 p3 ON p3.item_uuid = m.item_uuid
-- ttpos 导出用 LEFT JOIN，不过滤 pp.delete_time（已删除的商品也算销售）
-- 但本报表必须按 product_type 区分套餐/单品 sheet，所以仍 INNER JOIN，去掉 delete_time 过滤
JOIN `{{project}}`.`{{dataset}}`.`ttpos_product_package` pp
  ON pp.uuid = m.item_uuid
WHERE pp.product_type = {{product_type}}
  AND m.qty > 0
"""

COMBO_ORDERS_SQL = _PROFIT_SALES_TPL.replace("{product_type}", "1")
SINGLE_ORDERS_SQL = _PROFIT_SALES_TPL.replace("{product_type}", "0")

# 产品 BOM + 套餐结构 SQL 真源在 semantic.entities.{bom,combo}
BOM_SQL = bom.bom_sql()
COMBO_STRUCTURE_SQL = combo.combo_structure_sql()


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


def _resolve_base_unit_price(material_code, bq_price, uploaded_prices, erp_prices):
    """返回物料在基准单位下的单价。
    优先级：1) 上传价格清单 2) ERPNext Item Price 3) BQ 缺省价
    """
    if not material_code:
        return float(bq_price or 0)

    # 1) 上传价格清单（最高优先级）
    if uploaded_prices:
        for key in (material_code, material_code.upper(), material_code.lower()):
            if key in uploaded_prices:
                return uploaded_prices[key]

    # 2) ERPNext Item Price
    if erp_prices:
        for key in (material_code, material_code.upper(), material_code.lower()):
            if key in erp_prices:
                price, _uom = erp_prices[key]
                for corr_key in (material_code, material_code.upper(), material_code.lower()):
                    if corr_key in BOM_UNIT_CORRECTIONS:
                        price = price / BOM_UNIT_CORRECTIONS[corr_key]
                        break
                return price

    # 3) BQ 缺省价
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

def aggregate_with_bom(order_rows, bom_data, combo_structure, uploaded_prices=None, erp_prices=None, mode="combo"):
    """
    聚合订单和 BOM 数据（中间表版：保留所有原始字段，不预计算）。

    Args:
        order_rows: 引擎返回的订单行（已带 store_num, store_name）
        bom_data: {store_num: {item_uuid: [(material_code, ...), ...]}}
        combo_structure: {store_num: {combo_uuid: [child_uuid, ...]}}
        uploaded_prices: 上传价格清单 {material_code: price}
        erp_prices: ERPNext 价格 {material_code: (price, uom)}
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
        sales_price = float(getattr(row, "sales_price", None) or 0)
        original_amount = float(getattr(row, "original_amount", None) or 0)
        avg_member_discount = float(getattr(row, "avg_member_discount", None) or 1.0)
        free_qty = float(getattr(row, "free_qty", None) or 0)
        give_qty = float(getattr(row, "give_qty", None) or 0)
        refund_qty = float(getattr(row, "refund_qty", None) or 0)
        refund_amount = float(getattr(row, "refund_amount", None) or 0)
        cancelled_qty = float(getattr(row, "cancelled_qty", None) or 0)
        cancelled_amount = float(getattr(row, "cancelled_amount", None) or 0)
        list_price = float(getattr(row, "list_price", None) or 0)
        price_1 = getattr(row, "price_1", None)
        qty_1 = getattr(row, "qty_1", None)
        price_2 = getattr(row, "price_2", None)
        qty_2 = getattr(row, "qty_2", None)
        price_3 = getattr(row, "price_3", None)
        qty_3 = getattr(row, "qty_3", None)
        other_price_qty = getattr(row, "other_price_qty", None)

        key = (store_num, store_name, item_uuid, item_name)
        if key not in data:
            data[key] = {
                "qty": 0.0,
                "revenue": 0.0,
                "sales_price": 0.0,
                "original_amount": 0.0,
                "refund_qty": 0.0,
                "refund_amount": 0.0,
                "cancelled_qty": 0.0,
                "cancelled_amount": 0.0,
                "avg_member_discount": 0.0,
                "free_qty": 0.0,
                "give_qty": 0.0,
                "list_price": list_price,
                "price_1": price_1,
                "qty_1": qty_1,
                "price_2": price_2,
                "qty_2": qty_2,
                "price_3": price_3,
                "qty_3": qty_3,
                "other_price_qty": other_price_qty,
                "bom": {},
            }
        data[key]["qty"] += qty
        data[key]["revenue"] += revenue
        data[key]["sales_price"] += sales_price
        data[key]["original_amount"] += original_amount
        data[key]["refund_qty"] += refund_qty
        data[key]["refund_amount"] += refund_amount
        data[key]["cancelled_qty"] += cancelled_qty
        data[key]["cancelled_amount"] += cancelled_amount
        # 加权平均会员折扣率
        data[key]["avg_member_discount"] += avg_member_discount * qty
        data[key]["free_qty"] += free_qty
        data[key]["give_qty"] += give_qty

    # 归一化加权平均折扣率
    for key, val in data.items():
        if val["qty"] > 0:
            val["avg_member_discount"] = val["avg_member_discount"] / val["qty"]

    # 为每个 item 匹配 BOM
    for key, val in data.items():
        store_num, store_name, item_uuid, item_name = key
        store_boms = bom_data.get(store_num, {})

        if mode == "combo":
            # 套餐：合并所有子产品的 BOM。同一物料被多个子商品共用时累加 num。
            store_struct = combo_structure.get(store_num, {})
            child_uuids = store_struct.get(item_uuid, [])
            for child_uuid in child_uuids:
                child_bom = store_boms.get(child_uuid, [])
                for material_code, material_name, bom_num, bom_unit, conv_rate, bq_price in child_bom:
                    if not material_code:
                        continue
                    base_price = _resolve_base_unit_price(material_code, bq_price, uploaded_prices, erp_prices)
                    unit_price = base_price * (conv_rate or 1)
                    if material_code in val["bom"]:
                        prev_name, prev_num, prev_up, prev_unit = val["bom"][material_code]
                        val["bom"][material_code] = (
                            prev_name or material_name,
                            prev_num + bom_num,
                            unit_price,                  # 同物料同单位价不变
                            prev_unit or bom_unit,
                        )
                    else:
                        val["bom"][material_code] = (material_name, bom_num, unit_price, bom_unit)
        else:
            # 单品：直接匹配 BOM。同一商品多个 product_bom（不同 flavor/sauce 但共用 bom_card）
            # 会重复返回相同 material_code，按 dedup 处理（避免 N 倍虚增）。
            item_bom = store_boms.get(item_uuid, [])
            for material_code, material_name, bom_num, bom_unit, conv_rate, bq_price in item_bom:
                if not material_code:
                    continue
                if material_code not in val["bom"]:
                    base_price = _resolve_base_unit_price(material_code, bq_price, uploaded_prices, erp_prices)
                    unit_price = base_price * (conv_rate or 1)
                    val["bom"][material_code] = (material_name, bom_num, unit_price, bom_unit)

    # 转换为列表格式（保留所有原始字段，利润指标由 Excel 公式计算）
    result = {}
    for key, val in data.items():
        result[key] = {
            "qty": val["qty"],
            "revenue": val["revenue"],
            "sales_price": val["sales_price"],
            "original_amount": val["original_amount"],
            "refund_qty": val["refund_qty"],
            "refund_amount": val["refund_amount"],
            "cancelled_qty": val["cancelled_qty"],
            "cancelled_amount": val["cancelled_amount"],
            "avg_member_discount": val["avg_member_discount"],
            "free_qty": val["free_qty"],
            "give_qty": val["give_qty"],
            "list_price": val["list_price"],
            "price_1": val["price_1"],
            "qty_1": val["qty_1"],
            "price_2": val["price_2"],
            "qty_2": val["qty_2"],
            "price_3": val["price_3"],
            "qty_3": val["qty_3"],
            "other_price_qty": val["other_price_qty"],
            "bom": [
                (code, name, bom_num, price, uom)
                for code, (name, bom_num, price, uom) in val["bom"].items()
            ],
        }
    return result


# ============================================================================
# 扁平化行构建
# ============================================================================

def _build_rows(agg_data, mode, fallback_boms=None, uploaded_prices=None, erp_prices=None):
    """
    中间表：只输出原始数据，所有计算交给 Excel。
    列结构（26列）:
      0-2:   门店编号、门店名称、商品名称
      3-12:  当前标价、销量、营业额、标准金额、实收金额、会员折扣率、赠品数量、赠送数量、退款数量、退款金额
      13-19: 价格1、销量1、价格2、销量2、价格3、销量3、其他价格销量
      20-24: BOM物品名称、BOM物品编码、消耗数量、物料单价、单位
      25:    商品UUID(隐藏)
    """
    rows = []
    for (store_num, store_name, item_uuid, item_name), data in sorted(agg_data.items()):
        qty = data["qty"]
        revenue = data["revenue"]
        sales_price = data["sales_price"]
        original_amount = data["original_amount"]
        refund_qty = data["refund_qty"]
        refund_amount = data["refund_amount"]
        cancelled_qty = data.get("cancelled_qty", 0)
        cancelled_amount = data.get("cancelled_amount", 0)
        avg_member_discount = data["avg_member_discount"]
        free_qty = data["free_qty"]
        give_qty = data["give_qty"]
        list_price = data["list_price"]
        price_1 = data.get("price_1")
        qty_1 = data.get("qty_1")
        price_2 = data.get("price_2")
        qty_2 = data.get("qty_2")
        price_3 = data.get("price_3")
        qty_3 = data.get("qty_3")
        other_price_qty = data.get("other_price_qty")
        bom_list = data["bom"]

        # Fallback BOM 补充
        if not bom_list and fallback_boms:
            matched = _match_fallback_bom(item_name, fallback_boms)
            if matched:
                bom_list = []
                for code, name, bom_num, uom in matched:
                    unit_price = _resolve_base_unit_price(code, 0, uploaded_prices, erp_prices)
                    bom_list.append((code, name, bom_num, unit_price, uom or "-"))
                print(f"  [Fallback] {item_name}: 补充 {len(bom_list)} 个物料")

        # row 末尾扩展槽位（field_index 26-29 = utility 公式列；30-31 = 标价应收/异常损失公式列；
        # 32 = 取消数量、33 = 取消金额）。utility 列由引擎写公式，row 内填 None 占位即可。
        tail = [None, None, None, None, None, None,
                round(cancelled_qty, 2), round(cancelled_amount, 2)]

        if not bom_list:
            rows.append([
                store_num, store_name, item_name,
                round(list_price, 2), round(qty, 2),
                round(sales_price, 2), round(original_amount, 2),
                round(revenue, 2), round(avg_member_discount, 4),
                round(free_qty, 2), round(give_qty, 2),
                round(refund_qty, 2), round(refund_amount, 2),
                price_1, qty_1, price_2, qty_2, price_3, qty_3, other_price_qty,
                "-", "-", None, None, "-",
                str(item_uuid),
            ] + tail)
            continue

        for code, name, bom_num, mat_price, uom in bom_list:
            rows.append([
                store_num, store_name, item_name,
                round(list_price, 2), round(qty, 2),
                round(sales_price, 2), round(original_amount, 2),
                round(revenue, 2), round(avg_member_discount, 4),
                round(free_qty, 2), round(give_qty, 2),
                round(refund_qty, 2), round(refund_amount, 2),
                price_1, qty_1, price_2, qty_2, price_3, qty_3, other_price_qty,
                name, code, round(bom_num, 4), round(mat_price, 4), uom or "-",
                str(item_uuid),
            ] + tail)

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
    parser.add_argument("--price-list", default=None, help="上传的物料价格清单 Excel 路径（最高优先级）")
    parser.add_argument("--no-cache", action="store_true", help="禁用缓存")
    args = parser.parse_args()

    # 时间范围解析
    if args.month and (args.start_date or args.end_date):
        print("[错误] --month 与 --start-date/--end-date 不能同时使用")
        return 1

    if args.month:
        start_ts, end_ts = _month_to_ts_range(args.month)
        range_label = args.month.replace("-", "")
    elif args.start_date and args.end_date:
        start_dt = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=BKK_TZ)
        end_dt = datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=BKK_TZ) + timedelta(days=1)
        range_label = f"{args.start_date.replace('-', '')}_{args.end_date.replace('-', '')}"
        start_ts = int(start_dt.timestamp())
        end_ts = int(end_dt.timestamp())
    else:
        print("[错误] 必须指定 --month 或 --start-date + --end-date")
        return 1

    # 自动推导输出路径
    output_path = args.output or f"exports/profit_{range_label}.xlsx"

    # 初始化引擎
    engine = ReportEngine(project_id=args.project)

    # 加载资源配置
    config = load_config(args.config)

    # 加载上传价格清单（最高优先级）
    uploaded_prices = {}
    if args.price_list:
        uploaded_prices, _ = _load_uploaded_prices(args.price_list)
        print()

    # 加载 ERPNext 价格（带缓存，fallback）
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
            uploaded_prices=uploaded_prices, erp_prices=erp_prices, mode=mode
        )

        # 扁平化
        flat_rows = _build_rows(agg_data, mode, fallback_boms=fallback_boms,
                                uploaded_prices=uploaded_prices, erp_prices=erp_prices)

        # 加载列配置并写入 Excel
        sheet_cfg = engine.load_sheet_config(args.column_config, item_label)
        engine.write_sheet(wb, item_label, sheet_cfg, flat_rows)

        print(f"\n[{item_label}] 总明细行数: {len(flat_rows)} 行")
        print(f"[{item_label}] 此为中间表，利润指标请自行在 Excel 中定义")

    wb.close()
    print(f"\n输出文件: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
