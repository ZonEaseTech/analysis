#!/usr/bin/env python3
"""
利润报表导出 —— 套餐/单品利润分析（配置驱动版）

利用 report_engine 封装并发查询和 Excel 写入，报表脚本只关注：
  1. SQL 模板
  2. 聚合逻辑（去重、分组、BOM 展开）
  3. 业务规则（价格解析、fallback BOM 匹配）

Excel 列定义、合并规则、公式规则全部外置到 YAML：
  resources/reports/profit_margin.yaml

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
# SQL 模板
# ============================================================================

COMBO_SQL = """
WITH
combo_parent AS (
  SELECT
    sop.uuid AS parent_sop_uuid,
    sop.product_package_uuid AS combo_uuid,
    JSON_EXTRACT_SCALAR(pp.name, '$.zh') AS combo_name,
    sop.num * sop.copy_num * IF(sop.unit_num = 0, 1, sop.unit_num) AS combo_qty,
    sop.total_price AS combo_revenue
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
)

SELECT
  cp.parent_sop_uuid,
  cp.combo_uuid,
  cp.combo_name,
  cp.combo_qty,
  cp.combo_revenue,
  JSON_EXTRACT_SCALAR(child_pp.name, '$.zh') AS child_name,
  m.code AS material_code,
  JSON_EXTRACT_SCALAR(m.name, '$.zh') AS material_name,
  rm.num AS bom_num,
  rm_base.base_unit_conversion_rate AS material_conversion_rate,
  m.price AS material_bq_price,
  (child_sop.num * child_sop.copy_num * IF(child_sop.unit_num = 0, 1, child_sop.unit_num)) AS child_qty
FROM combo_parent cp
JOIN `{project}`.`{dataset}`.`ttpos_sale_order_product` child_sop
  ON child_sop.package_uuid = cp.parent_sop_uuid
  AND child_sop.delete_time = 0
  AND child_sop.cancel_time = 0
  AND child_sop.status = 1
JOIN `{project}`.`{dataset}`.`ttpos_product_package` child_pp
  ON child_pp.uuid = child_sop.product_package_uuid AND child_pp.delete_time = 0
JOIN `{project}`.`{dataset}`.`ttpos_sale_order_product_bom` sopb
  ON sopb.sale_order_product_uuid = child_sop.uuid AND sopb.delete_time = 0
JOIN `{project}`.`{dataset}`.`ttpos_product_bom` pb
  ON pb.uuid = sopb.product_bom_uuid AND pb.delete_time = 0
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
"""

SINGLE_SQL = """
WITH
single_sales AS (
  SELECT
    sop.uuid AS sop_uuid,
    sop.product_package_uuid AS product_uuid,
    JSON_EXTRACT_SCALAR(pp.name, '$.zh') AS product_name,
    sop.num * sop.copy_num * IF(sop.unit_num = 0, 1, sop.unit_num) AS product_qty,
    sop.total_price AS product_revenue
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
)

SELECT
  ss.sop_uuid,
  ss.product_uuid,
  ss.product_name,
  ss.product_qty,
  ss.product_revenue,
  m.code AS material_code,
  JSON_EXTRACT_SCALAR(m.name, '$.zh') AS material_name,
  rm.num AS bom_num,
  rm_base.base_unit_conversion_rate AS material_conversion_rate,
  m.price AS material_bq_price,
  ss.product_qty AS sale_qty
FROM single_sales ss
JOIN `{project}`.`{dataset}`.`ttpos_product_bom` pb
  ON pb.product_package_uuid = ss.product_uuid AND pb.delete_time = 0
JOIN `{project}`.`{dataset}`.`ttpos_sale_order_product_bom` sopb
  ON sopb.product_bom_uuid = pb.uuid AND sopb.sale_order_product_uuid = ss.sop_uuid AND sopb.delete_time = 0
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
# 数据聚合
# ============================================================================

def aggregate_combo_rows(raw_rows, erp_prices=None):
    """聚合套餐数据为明细行格式。按 parent_sop_uuid 去重。"""
    instance_totals = {}
    for row in raw_rows:
        instance_key = (row.store_num, row.store_name, row.parent_sop_uuid)
        if instance_key not in instance_totals:
            instance_totals[instance_key] = {
                "combo_uuid": row.combo_uuid,
                "combo_name": row.combo_name,
                "qty": float(row.combo_qty or 0),
                "revenue": float(row.combo_revenue or 0),
            }

    data = defaultdict(lambda: {"qty": 0.0, "revenue": 0.0, "bom": {}})
    for instance_key, totals in instance_totals.items():
        store_num, store_name, _ = instance_key
        key = (store_num, store_name, totals["combo_uuid"], totals["combo_name"])
        data[key]["qty"] += totals["qty"]
        data[key]["revenue"] += totals["revenue"]

    for row in raw_rows:
        key = (row.store_num, row.store_name, row.combo_uuid, row.combo_name)
        material_code = row.material_code
        if not material_code:
            continue
        if material_code not in data[key]["bom"]:
            material_name = row.material_name
            bom_num = float(row.bom_num or 0)
            unit_price, uom = _resolve_price(material_code, row.material_bq_price, erp_prices)
            cost_per_unit = bom_num * unit_price
            data[key]["bom"][material_code] = (material_name, bom_num, unit_price, uom, cost_per_unit)

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


def aggregate_single_rows(raw_rows, erp_prices=None):
    """聚合单品数据为明细行格式。按 sop_uuid 去重。"""
    instance_totals = {}
    for row in raw_rows:
        instance_key = (row.store_num, row.store_name, row.sop_uuid)
        if instance_key not in instance_totals:
            instance_totals[instance_key] = {
                "product_uuid": row.product_uuid,
                "product_name": row.product_name,
                "qty": float(row.product_qty or 0),
                "revenue": float(row.product_revenue or 0),
            }

    data = defaultdict(lambda: {"qty": 0.0, "revenue": 0.0, "bom": {}})
    for instance_key, totals in instance_totals.items():
        store_num, store_name, _ = instance_key
        key = (store_num, store_name, totals["product_uuid"], totals["product_name"])
        data[key]["qty"] += totals["qty"]
        data[key]["revenue"] += totals["revenue"]

    for row in raw_rows:
        key = (row.store_num, row.store_name, row.product_uuid, row.product_name)
        material_code = row.material_code
        if not material_code:
            continue
        if material_code not in data[key]["bom"]:
            material_name = row.material_name
            bom_num = float(row.bom_num or 0)
            unit_price, uom = _resolve_price(material_code, row.material_bq_price, erp_prices)
            cost_per_unit = bom_num * unit_price
            data[key]["bom"][material_code] = (material_name, bom_num, unit_price, uom, cost_per_unit)

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
        # 按月模式
        year, mon = int(args.month[:4]), int(args.month[5:7])
        start_dt = datetime(year, mon, 1, tzinfo=timezone.utc)
        if mon == 12:
            end_dt = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end_dt = datetime(year, mon + 1, 1, tzinfo=timezone.utc)
        range_label = args.month.replace("-", "")
    elif args.start_date and args.end_date:
        # 按日期范围模式
        start_dt = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        # 结束日期取当天的 23:59:59（即次日 00:00:00 作为 exclusive 边界）
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
        sql_template = COMBO_SQL if mode == "combo" else SINGLE_SQL
        print(f"\n========== 开始处理 {item_label} ==========\n")

        # 并发查询（引擎封装）
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

        # 聚合（报表脚本自定义逻辑）
        if mode == "combo":
            agg_data = aggregate_combo_rows(raw_rows, erp_prices=erp_prices)
        else:
            agg_data = aggregate_single_rows(raw_rows, erp_prices=erp_prices)

        # 扁平化
        flat_rows = _build_rows(agg_data, mode, fallback_boms=fallback_boms, erp_prices=erp_prices)

        # 加载列配置并写入 Excel（引擎封装）
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
