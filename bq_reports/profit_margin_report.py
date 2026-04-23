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
    key = cache_key("store_names", {"path": mapping_config.get("path", "")})
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
            if num is not None and name:
                mapping[str(num).strip()] = str(name).strip()
        set_cache(key, mapping)
        print(f"[Store Names] 加载 {len(mapping)} 个门店名称")
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

    key = cache_key("fallback_boms", {"path": bom_config.get("path", "")})
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

        set_cache(key, boms)
        print(f"[Fallback BOM] 加载 {len(boms)} 个商品的补充 BOM")
        return boms

    except Exception as e:
        print(f"[Fallback BOM] 加载失败: {e}")
        return {}


def _match_fallback_bom(item_name, fallback_boms):
    """用 BQ 商品名匹配 fallback BOM 中的商品名。"""
    if not item_name or not fallback_boms:
        return None
    for key in fallback_boms:
        if item_name in key or key.startswith(item_name):
            return fallback_boms[key]
    for key in fallback_boms:
        if len(item_name) >= 5 and item_name[:10] in key:
            return fallback_boms[key]
    return None


# ============================================================================
# 商家列表加载（适配器 + 缓存）
# ============================================================================

def _load_merchants(config: dict, store_names: dict, override_path: str = None):
    """从配置加载商家列表。支持缓存。"""
    merchant_cfg = config.get("merchant_list")
    if merchant_cfg:
        cache_ttl = config.get("cache", {}).get("merchant_list_ttl", 86400)
        key = cache_key("merchants", {"path": merchant_cfg.get("path", "")})
        cached = get_cache(key, ttl_seconds=cache_ttl)
        if cached is not None:
            print(f"[Merchants] 缓存命中: {len(cached)} 个")
            return cached

        adapter = get_adapter(merchant_cfg["adapter"])
        records = adapter.load(merchant_cfg)
        merchants = []
        for r in records:
            account = r.get("account")
            uuid_str = r.get("uuid")
            if account and uuid_str:
                account = str(account).strip()
                uuid_str = str(uuid_str).strip()
                m = re.search(r'admin-(\d+)@', account)
                store_num = m.group(1) if m else account
                store_name = store_names.get(store_num, "-")
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

# 套餐订单聚合（BQ 内完成，只返回产品级汇总）
COMBO_ORDERS_SQL = """
SELECT
  sop.product_package_uuid AS item_uuid,
  JSON_EXTRACT_SCALAR(pp.name, '$.zh') AS item_name,
  SUM(sop.num * sop.copy_num * IF(sop.unit_num = 0, 1, sop.unit_num)) AS qty,
  SUM(sop.total_price) AS revenue
FROM `{project}`.`{dataset}`.`ttpos_sale_order_product` sop
JOIN `{project}`.`{dataset}`.`ttpos_sale_bill` sb
  ON sb.uuid = sop.sale_bill_uuid AND sb.delete_time = 0
JOIN `{project}`.`{dataset}`.`ttpos_product_package` pp
  ON pp.uuid = sop.product_package_uuid AND pp.delete_time = 0
WHERE sop.delete_time = 0
  AND sop.cancel_time = 0
  AND sop.status = 1
  AND sb.status = 1
  AND sb.finish_time >= {start_ts}
  AND sb.finish_time < {end_ts}
  AND sop.product_type = 1
GROUP BY item_uuid, item_name
"""

# 单品订单聚合
SINGLE_ORDERS_SQL = """
SELECT
  sop.product_package_uuid AS item_uuid,
  JSON_EXTRACT_SCALAR(pp.name, '$.zh') AS item_name,
  SUM(sop.num * sop.copy_num * IF(sop.unit_num = 0, 1, sop.unit_num)) AS qty,
  SUM(sop.total_price) AS revenue
FROM `{project}`.`{dataset}`.`ttpos_sale_order_product` sop
JOIN `{project}`.`{dataset}`.`ttpos_sale_bill` sb
  ON sb.uuid = sop.sale_bill_uuid AND sb.delete_time = 0
JOIN `{project}`.`{dataset}`.`ttpos_product_package` pp
  ON pp.uuid = sop.product_package_uuid AND pp.delete_time = 0
WHERE sop.delete_time = 0
  AND sop.cancel_time = 0
  AND sop.status = 1
  AND sb.status = 1
  AND sb.finish_time >= {start_ts}
  AND sb.finish_time < {end_ts}
  AND sop.product_type = 0
GROUP BY item_uuid, item_name
"""

# 产品 BOM 结构（不关联订单表，纯产品级数据）
BOM_SQL = """
SELECT
  pb.product_package_uuid AS item_uuid,
  m.code AS material_code,
  JSON_EXTRACT_SCALAR(m.name, '$.zh') AS material_name,
  rm.num AS bom_num,
  rm_base.base_unit_conversion_rate AS material_conversion_rate,
  m.price AS material_bq_price
FROM `{project}`.`{dataset}`.`ttpos_product_bom` pb
LEFT JOIN `{project}`.`{dataset}`.`ttpos_related_material` rm
  ON (
    (pb.product_bom_card_uuid > 0 AND rm.related_uuid = pb.product_bom_card_uuid)
    OR (pb.product_bom_card_uuid = 0 AND rm.related_uuid = pb.uuid)
  )
  AND rm.delete_time = 0
LEFT JOIN `{project}`.`{dataset}`.`ttpos_material` m
  ON m.uuid = rm.material_uuid AND m.delete_time = 0
LEFT JOIN (
  SELECT material_uuid, MAX(base_unit_conversion_rate) AS base_unit_conversion_rate
  FROM `{project}`.`{dataset}`.`ttpos_related_material`
  WHERE delete_time = 0
  GROUP BY material_uuid
) rm_base ON rm_base.material_uuid = m.uuid
WHERE pb.delete_time = 0
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


def _resolve_price(material_code, bq_price, erp_prices):
    """用 ERPNext 价格替换 BQ 价格，并处理已知的单位不匹配。"""
    if not erp_prices or not material_code:
        return float(bq_price or 0), ""
    for key in (material_code, material_code.upper(), material_code.lower()):
        if key in erp_prices:
            price, uom = erp_prices[key]
            for corr_key in (material_code, material_code.upper(), material_code.lower()):
                if corr_key in BOM_UNIT_CORRECTIONS:
                    price = price / BOM_UNIT_CORRECTIONS[corr_key]
                    break
            return price, uom
    return float(bq_price or 0), ""


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
    返回: {store_num: {item_uuid: [(material_code, material_name, bom_num, conv_rate, bq_price), ...]}}
    """
    cfg = config or {}
    cache_ttl = cfg.get("cache", {}).get("bom_ttl", 86400)  # 1天
    key = cache_key("boms", {"count": len(merchants)})
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
    for row in raw_rows:
        store_num = row.store_num
        if store_num not in boms:
            boms[store_num] = {}
        item_uuid = str(row.item_uuid)
        if item_uuid not in boms[store_num]:
            boms[store_num][item_uuid] = []
        material_code = row.material_code
        if material_code:
            boms[store_num][item_uuid].append((
                str(material_code),
                row.material_name or "",
                float(row.bom_num or 0),
                float(row.material_conversion_rate or 1),
                float(row.material_bq_price or 0),
            ))

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

        key = (store_num, store_name, item_uuid, item_name)
        if key not in data:
            data[key] = {"qty": 0.0, "revenue": 0.0, "bom": {}}
        data[key]["qty"] += qty
        data[key]["revenue"] += revenue

    # 为每个 item 匹配 BOM
    for key, val in data.items():
        store_num, store_name, item_uuid, item_name = key
        store_boms = bom_data.get(store_num, {})

        if mode == "combo":
            # 套餐：获取子产品，合并它们的 BOM
            store_struct = combo_structure.get(store_num, {})
            child_uuids = store_struct.get(item_uuid, [])
            for child_uuid in child_uuids:
                child_bom = store_boms.get(child_uuid, [])
                for material_code, material_name, bom_num, conv_rate, bq_price in child_bom:
                    if not material_code:
                        continue
                    if material_code not in val["bom"]:
                        unit_price, uom = _resolve_price(material_code, bq_price, erp_prices)
                        cost_per_unit = bom_num * unit_price
                        val["bom"][material_code] = (material_name, bom_num, unit_price, uom, cost_per_unit)
        else:
            # 单品：直接匹配 BOM
            item_bom = store_boms.get(item_uuid, [])
            for material_code, material_name, bom_num, conv_rate, bq_price in item_bom:
                if not material_code:
                    continue
                if material_code not in val["bom"]:
                    unit_price, uom = _resolve_price(material_code, bq_price, erp_prices)
                    cost_per_unit = bom_num * unit_price
                    val["bom"][material_code] = (material_name, bom_num, unit_price, uom, cost_per_unit)

    # 转换为列表格式（与旧版兼容）
    result = {}
    for key, val in data.items():
        result[key] = {
            "qty": val["qty"],
            "revenue": val["revenue"],
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
    每行 15 个元素，顺序与 YAML 配置中的 field_index 对应。
    """
    rows = []
    for (store_num, store_name, item_uuid, item_name), data in sorted(agg_data.items()):
        qty = data["qty"]
        revenue = data["revenue"]
        item_unit_price = round(revenue / qty, 2) if qty > 0 else 0
        bom_list = data["bom"]

        # Fallback BOM 补充
        if not bom_list and fallback_boms:
            matched = _match_fallback_bom(item_name, fallback_boms)
            if matched:
                bom_list = []
                for code, name, bom_num, uom in matched:
                    unit_price, resolved_uom = _resolve_price(code, 0, erp_prices)
                    if not resolved_uom and uom:
                        resolved_uom = uom
                    cost_per_unit = bom_num * unit_price
                    bom_list.append((code, name, bom_num, unit_price, resolved_uom, cost_per_unit))
                print(f"  [Fallback] {item_name}: 补充 {len(bom_list)} 个物料")

        if not bom_list:
            rows.append([
                store_num, store_name, item_name,
                round(qty, 2), item_unit_price, round(revenue, 2),
                "-", "-", 0, 0, "-", 0,
                0, 0, 0,
            ])
            continue

        per_unit_bom_cost = sum(cost for _, _, _, _, _, cost in bom_list)
        total_bom_cost = per_unit_bom_cost * qty
        gross_profit = revenue - total_bom_cost
        gross_margin = gross_profit / revenue if revenue > 0 else 0

        for code, name, bom_num, mat_price, uom, cost in bom_list:
            rows.append([
                store_num, store_name, item_name,
                round(qty, 2), item_unit_price, round(revenue, 2),
                name, code, round(mat_price, 4), round(bom_num, 4), uom or "-", round(cost * qty, 2),
                round(per_unit_bom_cost, 2), round(gross_profit, 2), round(gross_margin, 4),
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
        start_dt = datetime(year, mon, 1, tzinfo=timezone.utc)
        if mon == 12:
            end_dt = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end_dt = datetime(year, mon + 1, 1, tzinfo=timezone.utc)
        range_label = args.month.replace("-", "")
    elif args.start_date and args.end_date:
        start_dt = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
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
    merchants = _load_merchants(config, store_names, override_path=args.merchants)

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

    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)

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
        ws = wb.create_sheet(title=item_label)
        engine.write_sheet(ws, sheet_cfg, flat_rows)

        negative_count = sum(1 for r in flat_rows if r[13] is not None and r[13] < 0)
        print(f"\n[{item_label}] 总明细行数: {len(flat_rows)} 行")
        if negative_count > 0:
            print(f"[{item_label}] 负毛利率: {negative_count} 行")

    wb.save(output_path)
    print(f"\n输出文件: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
