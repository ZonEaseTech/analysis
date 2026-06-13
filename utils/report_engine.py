#!/usr/bin/env python3
"""
报表引擎 —— 封装 BigQuery 并发查询、外部资源加载、配置驱动 Excel 写入。

将"容易踩坑"的公共逻辑集中管理：
  - 代理设置 + BQ 连接
  - 多门店并发查询
  - 外部资源加载（adapter + cache）
  - Excel 样式（表头、边框、列宽、冻结首行）
  - 合并单元格
  - 公式列（行级 + block 级）
  - 条件格式（负值标红）

报表脚本只需关心：
  1. SQL 模板
  2. 聚合逻辑（去重、分组、BOM 展开）
  3. 列配置（YAML 或代码）
  4. 调用引擎执行

Usage:
    from utils.report_engine import ReportEngine, ColumnConfig, SheetConfig, query_all_shops

    # 1. 初始化
    engine = ReportEngine(project_id="diyl-407103")

    # 2. 并发查询
    raw_rows, errors = query_all_shops(
        engine.client, sql_template, merchants, start_ts, end_ts, workers=10,
        row_proxy_factory=lambda row, acc, num, name: RowProxy(row, acc, num, name)
    )

    # 3. 聚合（报表脚本自定义）
    agg_data = my_aggregate(raw_rows)
    rows = build_flat_rows(agg_data)   # 扁平化为 list[list]

    # 4. 加载列配置（YAML）
    sheet_cfg = engine.load_sheet_config("resources/reports/profit_margin.yaml", "套餐")

    # 5. 写入 Excel（xlsxwriter 流式，open → write → close）
    import xlsxwriter
    wb = xlsxwriter.Workbook("output.xlsx")
    engine.write_sheet(wb, "套餐", sheet_cfg, rows)
    wb.close()
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# 写出口用 xlsxwriter（流式、比 openpyxl 写快 5-10×）；读输入仍走 openpyxl。
import xlsxwriter

from utils.cache import get_cache, set_cache, cache_key
from utils.resource_adapter import get_adapter

# BQ 相关 import 延迟到 ReportEngine 初始化时，避免无 google-cloud-bigquery 环境报错


# ───────────────────────────────────────────────────────────────
# 数据模型
# ───────────────────────────────────────────────────────────────

@dataclass
class ColumnConfig:
    """Excel 列配置"""
    name: str
    field_index: int                    # 在行数据中的索引（0-based）
    col_type: str = "value"             # value | formula | block_formula
    format_str: str = ""                # 数字/百分比格式，如 "0.00", "0.00%"
    width: int = 15
    merge: bool = False                 # 是否合并（block 级别）
    formula_template: str = ""          # 公式模板，支持 {row} {block_start} {block_end} {col}
    negative_red: bool = False          # 负值标红（基于预计算值）
    positive_red: bool = False          # 正值标红（用于"异常损失"等"非零即异常"列；通过条件格式）
    zero_yellow: bool = False           # 0 值标黄（用于"应有值却缺失"列，如 strict 模式下物料单价=0）
    hidden: bool = False                # 列隐藏（客户报表收敛视图，audit 脚本仍可读）
    comment: str = ""                   # 表头悬停备注（字段语义 / 取数说明）
    money_satang: bool = False          # 值是萨当整数 (INT64), 写盘时 /100 还原成元
                                        # (PR-B 7b: 交易金额整数化, 唯一显示侧除法点)


@dataclass
class SheetConfig:
    """Sheet 配置"""
    name: str
    columns: List[ColumnConfig]
    merge_key_indices: List[int] = field(default_factory=list)  # block 判定字段索引
    freeze_panes: str = "A2"


# ───────────────────────────────────────────────────────────────
# Excel 样式（xlsxwriter format dict，缓存创建）
# ───────────────────────────────────────────────────────────────

_BORDER_COLOR = "#D9D9D9"
_HEADER_BG = "#4472C4"


class _FormatCache:
    """缓存 xlsxwriter Format 对象，避免重复创建（同样的 dict → 同一个 Format）。"""
    def __init__(self, workbook):
        self._wb = workbook
        self._cache = {}

    def get(self, **kwargs):
        # 标准化 + 缓存（dict 是 unhashable，转 frozenset of items）
        key = frozenset(kwargs.items())
        if key not in self._cache:
            self._cache[key] = self._wb.add_format(dict(kwargs))
        return self._cache[key]


def _xl_col_letter(col_idx_1based: int) -> str:
    """1-based col index → A, B, ..., Z, AA, AB, ..."""
    s = ""
    n = col_idx_1based
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# ───────────────────────────────────────────────────────────────
# 查询层
# ───────────────────────────────────────────────────────────────

def query_all_shops(
    client,
    sql_template: str,
    merchants: List[Tuple],
    start_ts: int,
    end_ts: int,
    workers: int = 10,
    row_proxy_factory: Optional[Callable] = None,
    label: str = "门店",
    sql_template_factory: Optional[Callable[[str], str]] = None,
) -> Tuple[List[Any], List[Dict]]:
    """
    并发查询所有门店。

    Args:
        client: BigQuery client
        sql_template: SQL 模板，含 {project} {dataset} {start_ts} {end_ts}
        merchants: [(account, uuid_str, store_num, store_name), ...]
        start_ts, end_ts: 时间范围（Unix 秒）
        workers: 并发线程数
        row_proxy_factory: 可选，(row, account, store_num, store_name) -> proxy_row
        label: 日志标签

    Returns:
        (all_raw_rows, errors)
    """
    all_rows = []
    errors = []

    def _query_one(account_uuid):
        account, uuid_str, store_num, store_name = account_uuid
        dataset = f"shop{uuid_str}"
        # 优先 per-store factory (用于按 dataset 切换模板, e.g. 测试营业过滤);
        # fallback 到固定 sql_template
        tpl = sql_template_factory(uuid_str) if sql_template_factory else sql_template
        sql = tpl.format(
            project=client.project,
            dataset=dataset,
            start_ts=start_ts,
            end_ts=end_ts,
        )
        try:
            rows = list(client.query(sql).result())
            if row_proxy_factory:
                rows = [row_proxy_factory(r, account, store_num, store_name) for r in rows]
            return {"account": account, "rows": rows, "error": None}
        except Exception as e:
            return {"account": account, "rows": [], "error": str(e)}

    max_workers = min(workers, len(merchants))
    print(f"[{label}] 并发查询: {max_workers} 线程")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_query_one, m): m[0] for m in merchants}
        for future in as_completed(futures):
            result = future.result()
            if result["error"]:
                errors.append({"account": result["account"], "error": result["error"]})
                print(f"[错误] {result['account']}: {result['error']}")
            else:
                all_rows.extend(result["rows"])
                print(f"[OK] {result['account']}: {len(result['rows'])} 行")

    print(f"\n[{label}] 查询完成: {len(merchants) - len(errors)}/{len(merchants)} 个门店成功")
    if errors:
        print(f"[{label}] 错误数: {len(errors)}")

    return all_rows, errors


# ───────────────────────────────────────────────────────────────
# 资源加载层
# ───────────────────────────────────────────────────────────────

def load_resources(resource_configs: List[Dict]) -> Dict[str, Any]:
    """
    加载多个外部资源，返回 name -> data 字典。

    resource_configs: [
        {"name": "store_names", "adapter": "excel", "path": "...", "mapping": {...}, "cache_ttl": 604800},
        ...
    ]
    """
    results = {}
    for cfg in resource_configs:
        name = cfg["name"]
        adapter_name = cfg["adapter"]
        cache_ttl = cfg.get("cache_ttl", 3600)

        cache_key_str = cache_key(name, {"path": cfg.get("path", "")})
        cached = get_cache(cache_key_str, ttl_seconds=cache_ttl)
        if cached is not None:
            print(f"[Resource] 缓存命中: {name}")
            results[name] = cached
            continue

        adapter = get_adapter(adapter_name)
        # 去掉 engine 专用字段再传给 adapter
        adapter_cfg = {k: v for k, v in cfg.items() if k not in ("name", "cache_ttl")}
        data = adapter.load(adapter_cfg)
        set_cache(cache_key_str, data)
        count = len(data) if hasattr(data, "__len__") else "?"
        print(f"[Resource] 加载: {name}, {count} 条")
        results[name] = data

    return results


# ───────────────────────────────────────────────────────────────
# Excel 写入层
# ───────────────────────────────────────────────────────────────

def _detect_blocks(rows: List[List], key_indices: List[int]) -> List[Tuple[int, int]]:
    """检测数据 block 边界。返回 [(start_idx, end_idx), ...]"""
    if not rows:
        return []
    if not key_indices:
        return [(0, len(rows) - 1)]

    blocks = []
    start = 0
    for i in range(1, len(rows)):
        if any(rows[i][k] != rows[i - 1][k] for k in key_indices):
            blocks.append((start, i - 1))
            start = i
    blocks.append((start, len(rows) - 1))
    return blocks


def _resolve_formula(template: str, row: int, block_start: int, block_end: int, col_letter: str) -> str:
    """替换公式模板变量。"""
    return (
        template
        .replace("{row}", str(row))
        .replace("{block_start}", str(block_start))
        .replace("{block_end}", str(block_end))
        .replace("{col}", col_letter)
    )


def write_configured_sheet(workbook, sheet_name: str, sheet_config: SheetConfig, rows: List[List]):
    """
    按配置写入 Sheet（xlsxwriter 实现）。

    Args:
        workbook: xlsxwriter.Workbook 对象
        sheet_name: 工作表名称
        sheet_config: SheetConfig（列定义、合并规则、公式规则）
        rows: 扁平数据行，每个内层列表长度 ≥ max(field_index) + 1
    """
    columns = sheet_config.columns
    merge_key_indices = sheet_config.merge_key_indices
    cache = _FormatCache(workbook)

    ws = workbook.add_worksheet(sheet_name)

    # 1. 表头（xlsxwriter row/col 全 0-based）
    hdr_fmt = cache.get(
        bold=True, bg_color=_HEADER_BG, font_color="white",
        align="center", valign="vcenter",
        border=1, border_color=_BORDER_COLOR,
    )
    for col_idx, col_cfg in enumerate(columns):
        ws.write(0, col_idx, col_cfg.name, hdr_fmt)
        if col_cfg.comment:
            ws.write_comment(0, col_idx, col_cfg.comment, {
                "author": "数据口径", "x_scale": 1.5, "y_scale": 1.6, "visible": False,
            })

    if not rows:
        for col_idx, col_cfg in enumerate(columns):
            ws.set_column(col_idx, col_idx, 15, None,
                          {"hidden": True} if col_cfg.hidden else {})
        if sheet_config.freeze_panes:
            ws.freeze_panes(1, 0)
        return

    # 2. 列宽（必须在 set_column 阶段，写完不能改）
    for col_idx, col_cfg in enumerate(columns):
        try:
            data_max = max(
                (len(str(rows[r][col_cfg.field_index] or "")) for r in range(len(rows))
                 if col_cfg.field_index < len(rows[r])),
                default=0,
            )
        except Exception:
            data_max = 0
        width = min(max(data_max, len(col_cfg.name)) + 4, 50)
        ws.set_column(col_idx, col_idx, width, None,
                      {"hidden": True} if col_cfg.hidden else {})

    # 3. 检测 block
    blocks = _detect_blocks(rows, merge_key_indices)
    merge_col_set = {i for i, c in enumerate(columns) if c.merge} if merge_key_indices else set()

    # 4. 写"非 merge"列（value / formula / block_formula）。merge 列稍后专门处理
    #    — xlsxwriter 的 merge_range 不能在已 write 过的 cell 上调用
    for block_start, block_end in blocks:
        excel_first = block_start + 2  # 公式里的 1-based 行号
        excel_last = block_end + 2

        for data_idx in range(block_start, block_end + 1):
            xl_row = data_idx + 1  # xlsxwriter 0-based: header=0, data 从 1 开始
            excel_row = data_idx + 2

            for col_idx, col_cfg in enumerate(columns):
                if col_idx in merge_col_set:
                    continue  # merge 列稍后处理
                field_idx = col_cfg.field_index

                if col_cfg.col_type == "value":
                    value = rows[data_idx][field_idx] if field_idx < len(rows[data_idx]) else None
                    # 萨当整数 → 元: 唯一显示侧除法点 (PR-B 7b)
                    if col_cfg.money_satang and isinstance(value, (int, float)):
                        value = value / 100.0
                    fmt_args = {"border": 1, "border_color": _BORDER_COLOR, "valign": "vcenter"}
                    if isinstance(value, (int, float)):
                        fmt_args["align"] = "right"
                        if col_cfg.format_str:
                            fmt_args["num_format"] = col_cfg.format_str
                    else:
                        fmt_args["align"] = "left"
                    fmt = cache.get(**fmt_args)
                    if value is None:
                        ws.write_blank(xl_row, col_idx, None, fmt)
                    else:
                        ws.write(xl_row, col_idx, value, fmt)

                elif col_cfg.col_type == "formula":
                    col_letter = _xl_col_letter(col_idx + 1)
                    formula = _resolve_formula(
                        col_cfg.formula_template, excel_row, excel_first, excel_last, col_letter
                    )
                    fmt_args = {"border": 1, "border_color": _BORDER_COLOR,
                                "align": "right", "valign": "vcenter"}
                    if col_cfg.format_str:
                        fmt_args["num_format"] = col_cfg.format_str
                    fmt = cache.get(**fmt_args)
                    ws.write_formula(xl_row, col_idx, formula, fmt)

        # block_formula 在 block 第一行（也要避开 merge 列；merge 列下面统一处理）
        for col_idx, col_cfg in enumerate(columns):
            if col_idx in merge_col_set:
                continue
            if col_cfg.col_type != "block_formula":
                continue
            col_letter = _xl_col_letter(col_idx + 1)
            formula = _resolve_formula(
                col_cfg.formula_template, excel_first, excel_first, excel_last, col_letter
            )
            fmt_args = {"border": 1, "border_color": _BORDER_COLOR,
                        "align": "center", "valign": "vcenter"}
            if col_cfg.format_str:
                fmt_args["num_format"] = col_cfg.format_str
            if col_cfg.negative_red:
                pre = rows[block_start][col_cfg.field_index] if col_cfg.field_index < len(rows[block_start]) else None
                if isinstance(pre, (int, float)) and pre < 0:
                    fmt_args["font_color"] = "#FF0000"
            fmt = cache.get(**fmt_args)
            xl_first = block_start + 1
            ws.write_formula(xl_first, col_idx, formula, fmt)

    # 5. merge 列（按 block 处理，单行不 merge 直接 write）
    for col_idx in sorted(merge_col_set):
        col_cfg = columns[col_idx]
        for block_start, block_end in blocks:
            xl_first = block_start + 1
            xl_last = block_end + 1
            excel_first = block_start + 2
            excel_last = block_end + 2

            # 准备值/公式 + 格式
            fmt_args = {"border": 1, "border_color": _BORDER_COLOR,
                        "align": "center", "valign": "vcenter"}
            if col_cfg.format_str:
                fmt_args["num_format"] = col_cfg.format_str
            if col_cfg.negative_red and col_cfg.col_type == "block_formula":
                pre = rows[block_start][col_cfg.field_index] if col_cfg.field_index < len(rows[block_start]) else None
                if isinstance(pre, (int, float)) and pre < 0:
                    fmt_args["font_color"] = "#FF0000"

            if col_cfg.col_type == "value":
                val = rows[block_start][col_cfg.field_index] if col_cfg.field_index < len(rows[block_start]) else None
                # 萨当整数 → 元: 唯一显示侧除法点 (PR-B 7b)
                if col_cfg.money_satang and isinstance(val, (int, float)):
                    val = val / 100.0
                if isinstance(val, (int, float)):
                    fmt_args["align"] = "right"
                fmt = cache.get(**fmt_args)
                if xl_first == xl_last:
                    if val is None:
                        ws.write_blank(xl_first, col_idx, None, fmt)
                    else:
                        ws.write(xl_first, col_idx, val, fmt)
                else:
                    ws.merge_range(xl_first, col_idx, xl_last, col_idx, val, fmt)
            else:
                # formula / block_formula：合并区域只在第一行写公式
                col_letter = _xl_col_letter(col_idx + 1)
                # row 参考 = excel_first（block 起始行）
                formula = _resolve_formula(
                    col_cfg.formula_template, excel_first, excel_first, excel_last, col_letter
                )
                fmt = cache.get(**fmt_args)
                if xl_first == xl_last:
                    ws.write_formula(xl_first, col_idx, formula, fmt)
                else:
                    # merge_range 本身支持写入公式，直接传公式即可
                    ws.merge_range(xl_first, col_idx, xl_last, col_idx, formula, fmt)

    # 6. 条件格式：positive_red 列在数据范围内 > 0 时整格标红
    last_data_row = len(rows)  # xlsxwriter 0-based 末行 = len(rows)
    red_fmt = workbook.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006", "bold": True})
    for col_idx, col_cfg in enumerate(columns):
        if not col_cfg.positive_red:
            continue
        ws.conditional_format(1, col_idx, last_data_row, col_idx, {
            "type": "cell", "criteria": ">", "value": 0, "format": red_fmt,
        })

    # 6b. 条件格式：zero_yellow 列 <= 0 时整格标黄（"应有值却缺失"，如物料单价=0）
    yellow_fmt = workbook.add_format({"bg_color": "#FFEB9C", "font_color": "#9C6500"})
    for col_idx, col_cfg in enumerate(columns):
        if not col_cfg.zero_yellow:
            continue
        ws.conditional_format(1, col_idx, last_data_row, col_idx, {
            "type": "cell", "criteria": "<=", "value": 0, "format": yellow_fmt,
        })

    # 7. 冻结首行
    if sheet_config.freeze_panes:
        # freeze_panes 的字符串 'A2' 转 (row=1, col=0)
        ws.freeze_panes(1, 0)


# ───────────────────────────────────────────────────────────────
# 配置加载
# ───────────────────────────────────────────────────────────────

def load_sheet_config(yaml_path: str, sheet_name: str) -> SheetConfig:
    """从 YAML 加载 Sheet 配置。"""
    import yaml
    with open(yaml_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    sheet_data = config["sheets"][sheet_name]
    columns = []
    for c in sheet_data.get("columns", []):
        columns.append(ColumnConfig(
            name=c["name"],
            field_index=c.get("field_index", 0),
            col_type=c.get("type", "value"),
            format_str=c.get("format", ""),
            width=c.get("width", 15),
            merge=c.get("merge", False),
            formula_template=c.get("formula", ""),
            negative_red=c.get("negative_red", False),
            positive_red=c.get("positive_red", False),
            zero_yellow=c.get("zero_yellow", False),
            hidden=c.get("hidden", False),
            comment=c.get("comment", ""),
            money_satang=c.get("money_satang", False),
        ))

    return SheetConfig(
        name=sheet_name,
        columns=columns,
        merge_key_indices=sheet_data.get("merge_key_indices", []),
        freeze_panes=sheet_data.get("freeze_panes", "A2"),
    )


# ───────────────────────────────────────────────────────────────
# 引擎类（便利封装）
# ───────────────────────────────────────────────────────────────

class ReportEngine:
    """报表引擎便利封装。"""

    def __init__(self, project_id: str = "diyl-407103"):
        # 延迟 import，避免无 BQ 环境时模块加载失败
        from bq_reports.utils.bq_client import get_bq_client, setup_proxy
        setup_proxy()
        self.client = get_bq_client(project_id)

    def query(self, sql_template, merchants, start_ts, end_ts, workers=10,
              row_proxy_factory=None, label="门店", sql_template_factory=None):
        """并发查询。sql_template_factory(uuid_str) -> str 时, 按店切换模板。"""
        return query_all_shops(
            self.client, sql_template, merchants, start_ts, end_ts,
            workers=workers, row_proxy_factory=row_proxy_factory, label=label,
            sql_template_factory=sql_template_factory,
        )

    def load_resources(self, configs: List[Dict]) -> Dict[str, Any]:
        """加载外部资源。"""
        return load_resources(configs)

    def write_sheet(self, workbook, sheet_name: str, sheet_config: SheetConfig, rows: List[List]):
        """按配置写入 Sheet（xlsxwriter）。

        Args:
            workbook: xlsxwriter.Workbook
            sheet_name: 工作表名称（也是 add_worksheet 的名字）
            sheet_config: SheetConfig
            rows: 扁平数据行
        """
        return write_configured_sheet(workbook, sheet_name, sheet_config, rows)

    def load_sheet_config(self, yaml_path: str, sheet_name: str) -> SheetConfig:
        """从 YAML 加载配置。"""
        return load_sheet_config(yaml_path, sheet_name)
