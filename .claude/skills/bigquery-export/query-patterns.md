# BigQuery 查询模式库

> SQL 模式用于 BQ 控制台直接执行；Python 模式用于复杂聚合 + Excel 导出

---

## SQL 模式

### 1. JSON 多语言提取

```sql
-- 提取中文
JSON_EXTRACT_SCALAR(t.name, '$.zh') AS name_zh

-- 提取泰文
JSON_EXTRACT_SCALAR(t.name, '$.th') AS name_th

-- 提取多种语言
JSON_EXTRACT_SCALAR(t.name, '$.zh') AS name_zh,
JSON_EXTRACT_SCALAR(t.name, '$.th') AS name_th,
JSON_EXTRACT_SCALAR(t.name, '$.en') AS name_en
```

### 2. 时间戳转换

```sql
-- Unix 秒 → 可读时间（UTC+7 泰国时区）
FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S', TIMESTAMP_SECONDS(t.create_time), 'Asia/Bangkok') AS created_at

-- 按天分组
FORMAT_TIMESTAMP('%Y-%m-%d', TIMESTAMP_SECONDS(t.create_time), 'Asia/Bangkok') AS date

-- 按月分组
FORMAT_TIMESTAMP('%Y-%m', TIMESTAMP_SECONDS(t.create_time), 'Asia/Bangkok') AS month

-- 时间范围过滤
WHERE t.create_time BETWEEN UNIX_SECONDS(TIMESTAMP('2026-01-01')) AND UNIX_SECONDS(TIMESTAMP('2026-02-01'))
```

### 3. 字符串聚合（GROUP_CONCAT 等价）

```sql
-- 基本用法
STRING_AGG(expr, ', ') AS combined

-- 带排序
STRING_AGG(expr, ' || ' ORDER BY t.id) AS combined

-- 多字段拼接后聚合
STRING_AGG(
  CONCAT(
    JSON_EXTRACT_SCALAR(m.name, '$.zh'), '|',
    IFNULL(m.code, ''), '|',
    CAST(rm.num AS STRING), '|',
    IFNULL(JSON_EXTRACT_SCALAR(rm.unit_name, '$.zh'), '')
  ),
  ' || ' ORDER BY rm.id
) AS detail
```

### 4. 固定列数行转列（Pivot）

先查最大列数，再用 `ROW_NUMBER + MAX(IF)` 展开：

```sql
-- Step 1: 查最大列数
SELECT MAX(cnt) FROM (
  SELECT related_uuid, COUNT(*) AS cnt
  FROM `{p}`.`{d}`.`ttpos_related_material`
  WHERE delete_time = 0
  GROUP BY related_uuid
);

-- Step 2: 行转列（假设 max=5）
SELECT
  name,
  MAX(IF(rn = 1, m_name, NULL)) AS item_1_name,
  MAX(IF(rn = 1, m_code, NULL)) AS item_1_code,
  MAX(IF(rn = 1, m_num,  NULL)) AS item_1_qty,
  MAX(IF(rn = 2, m_name, NULL)) AS item_2_name,
  MAX(IF(rn = 2, m_code, NULL)) AS item_2_code,
  MAX(IF(rn = 2, m_num,  NULL)) AS item_2_qty,
  -- ...重复到 max
FROM (
  SELECT
    parent.name,
    child.name AS m_name,
    child.code AS m_code,
    child.num  AS m_num,
    ROW_NUMBER() OVER (PARTITION BY parent.uuid ORDER BY child.id) AS rn
  FROM ...
) sub
GROUP BY name
```

### 5. 条件聚合（分类汇总）

```sql
SELECT
  JSON_EXTRACT_SCALAR(c.name, '$.zh') AS category,
  COUNT(*) AS total,
  COUNTIF(pp.status = 0) AS active_count,
  COUNTIF(pp.status = 1) AS inactive_count,
  SUM(bom.stock_num) AS total_stock
FROM `{p}`.`{d}`.`ttpos_product_package` pp
JOIN `{p}`.`{d}`.`ttpos_product_bom` bom ON bom.product_package_uuid = pp.uuid AND bom.delete_time = 0
LEFT JOIN `{p}`.`{d}`.`ttpos_product_category` c ON c.uuid = pp.category_uuid AND c.delete_time = 0
WHERE pp.delete_time = 0
GROUP BY c.name
ORDER BY total DESC
```

### 6. 跨门店 UNION

```sql
SELECT 'shop_A' AS shop, t.* FROM `{p}`.`shop{uuid_a}`.`ttpos_material` t WHERE t.delete_time = 0
UNION ALL
SELECT 'shop_B' AS shop, t.* FROM `{p}`.`shop{uuid_b}`.`ttpos_material` t WHERE t.delete_time = 0
```

### 7. 窗口函数（排名/占比）

```sql
SELECT
  name,
  amount,
  RANK() OVER (ORDER BY amount DESC) AS rank,
  ROUND(amount / SUM(amount) OVER () * 100, 2) AS pct
FROM ...
```

---

## Python 模式

### 何时用 Python

| 场景 | 原因 |
|------|------|
| 动态列数行转列 | SQL 需要提前知道列数，Python 可自动适配 |
| 多 Sheet Excel | BQ 控制台只能导单表 CSV |
| 复杂后处理 | 计算占比、排名、条件格式、数据清洗 |
| 跨多店批量导出 | 循环多个 dataset |
| Excel 格式要求 | 合并单元格、冻结行、列宽、字体颜色 |
| 大数据量分批 | 避免 BQ 结果集过大 |

### Pattern A: 动态行转列 + Excel

适用于 BOM 导出、套餐成分导出等每行子项数量不固定的场景。

```python
#!/usr/bin/env python3
"""动态行转列导出 Excel
依赖: pip install google-cloud-bigquery openpyxl
认证: gcloud auth application-default login
"""
import argparse
from collections import defaultdict
from google.cloud import bigquery
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill

PROJECT = "diyl-407103"

def main():
    args = parse_args()
    client = bigquery.Client(project=args.project)
    rows = query_data(client, args.dataset)
    grouped = group_and_pivot(rows)
    write_excel(grouped, args.output)
    print(f"导出完成: {args.output}，共 {len(grouped['data'])} 行")

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--project", default=PROJECT)
    p.add_argument("--dataset", required=True, help="shop{company_uuid}")
    p.add_argument("--output", default="export.xlsx")
    # 按需添加更多过滤参数
    p.add_argument("--headquarter-uuid", type=int, default=0, help="总部 UUID，0=不过滤")
    return p.parse_args()

def query_data(client, dataset):
    """查询明细行（不做聚合，Python 侧处理）"""
    sql = f"""
    SELECT
      bom.uuid AS bom_uuid,
      JSON_EXTRACT_SCALAR(pp.name, '$.zh') AS product_name,
      JSON_EXTRACT_SCALAR(bom.name, '$.zh') AS spec_name,
      JSON_EXTRACT_SCALAR(m.name, '$.zh') AS material_name,
      IFNULL(m.code, '') AS material_code,
      rm.num AS material_qty,
      IFNULL(JSON_EXTRACT_SCALAR(rm.unit_name, '$.zh'), '') AS unit_name
    FROM `{client.project}`.`{dataset}`.`ttpos_product_bom` bom
    JOIN `{client.project}`.`{dataset}`.`ttpos_product_package` pp
      ON pp.uuid = bom.product_package_uuid AND pp.delete_time = 0
    JOIN `{client.project}`.`{dataset}`.`ttpos_product_bom_card` card
      ON card.uuid = bom.product_bom_card_uuid AND card.delete_time = 0
    JOIN `{client.project}`.`{dataset}`.`ttpos_related_material` rm
      ON rm.related_uuid = card.uuid AND rm.delete_time = 0
    JOIN `{client.project}`.`{dataset}`.`ttpos_material` m
      ON m.uuid = rm.material_uuid AND m.delete_time = 0
    WHERE bom.delete_time = 0
      AND bom.product_bom_card_uuid > 0
    ORDER BY bom.uuid, rm.id
    """
    return list(client.query(sql).result())

def group_and_pivot(rows):
    """将明细行按 bom_uuid 分组，动态展开为列"""
    groups = defaultdict(lambda: {"product_name": "", "spec_name": "", "materials": []})
    max_materials = 0

    for row in rows:
        key = row.bom_uuid
        g = groups[key]
        g["product_name"] = row.product_name or ""
        g["spec_name"] = row.spec_name or ""
        g["materials"].append({
            "name": row.material_name or "",
            "code": row.material_code or "",
            "qty": row.material_qty or 0,
            "unit": row.unit_name or "",
        })
        max_materials = max(max_materials, len(g["materials"]))

    # 构建表头
    headers = ["商品名称", "规格名称"]
    for i in range(1, max_materials + 1):
        headers.extend([f"物品{i}名称", f"物品{i}编码", f"物品{i}数量", f"物品{i}单位"])

    # 构建数据行
    data = []
    for g in groups.values():
        row = [g["product_name"], g["spec_name"]]
        for mat in g["materials"]:
            row.extend([mat["name"], mat["code"], mat["qty"], mat["unit"]])
        data.append(row)

    return {"headers": headers, "data": data}

def write_excel(result, output_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "BOM导出"

    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font = Font(bold=True, size=11, color="FFFFFF")

    # 写表头
    for col_idx, header in enumerate(result["headers"], 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    # 写数据
    for row_idx, row_data in enumerate(result["data"], 2):
        for col_idx, value in enumerate(row_data, 1):
            ws.cell(row=row_idx, column=col_idx, value=value)

    # 自动列宽
    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

    # 冻结首行
    ws.freeze_panes = "A2"

    wb.save(output_path)

if __name__ == "__main__":
    main()
```

### Pattern B: 多 Sheet 分类导出

适用于按分类、门店、时间段分 Sheet 导出。

```python
def write_multi_sheet(data_by_category, output_path):
    """data_by_category: dict[sheet_name, {"headers": [...], "data": [[...]]}]"""
    wb = Workbook()
    wb.remove(wb.active)  # 移除默认 Sheet

    for sheet_name, content in data_by_category.items():
        ws = wb.create_sheet(title=sheet_name[:31])  # Excel Sheet 名最长 31 字符
        # 写表头
        for col_idx, h in enumerate(content["headers"], 1):
            ws.cell(row=1, column=col_idx, value=h)
        # 写数据
        for row_idx, row in enumerate(content["data"], 2):
            for col_idx, val in enumerate(row, 1):
                ws.cell(row=row_idx, column=col_idx, value=val)
        ws.freeze_panes = "A2"

    wb.save(output_path)
```

### Pattern C: 跨门店批量导出

```python
def query_across_shops(client, shop_datasets, sql_template):
    """跨多个门店执行相同查询，合并结果"""
    all_rows = []
    for dataset in shop_datasets:
        sql = sql_template.format(project=client.project, dataset=dataset)
        rows = list(client.query(sql).result())
        for row in rows:
            all_rows.append({"shop": dataset, **dict(row)})
    return all_rows
```

### Pattern D: 带汇总行的报表

```python
def add_summary_row(ws, data_rows, sum_columns, row_offset=2):
    """在数据末尾添加汇总行"""
    summary_row = row_offset + len(data_rows)
    ws.cell(row=summary_row, column=1, value="合计").font = Font(bold=True)
    for col_idx in sum_columns:
        col_letter = ws.cell(row=1, column=col_idx).column_letter
        formula = f"=SUM({col_letter}{row_offset}:{col_letter}{summary_row - 1})"
        cell = ws.cell(row=summary_row, column=col_idx, value=formula)
        cell.font = Font(bold=True)
```

### Pattern E: 条件格式（高亮异常值）

```python
from openpyxl.formatting.rule import CellIsRule

def highlight_low_stock(ws, stock_col_letter, data_start_row, data_end_row, threshold=10):
    """库存低于阈值标红"""
    red_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    ws.conditional_formatting.add(
        f"{stock_col_letter}{data_start_row}:{stock_col_letter}{data_end_row}",
        CellIsRule(operator="lessThan", formula=[str(threshold)], fill=red_fill)
    )
```

### Pattern G: 门店外卖营业额 / 外卖实收（多条件去重）

业务规则（同一 `sale_bill` 只计一次，满足任一即外卖单）：

1. **支付方式**：该账单下任一 `sale_order` 的 `payment_order`（`related_type=0`、`status=1`）中，`payment_method_name` 去空格、小写后匹配 `robinhood|grab|lineman|shopee`。
2. **Grab / LINE MAN 渠道**：`order_source_uuid > 0`，且 `order_source` 多语言名或 `sale_bill.order_source_name` JSON 快照去空格、小写后含 `grab` 或 `lineman`。
3. **外卖/外送/打包**：`bill_type = 2`（会员外送）或 `order_source_uuid > 0` 或 `dining_method = 1`（打包）。

金额口径（与现有「总营业额」对齐时请在 SQL 中按需改为 `origin_amount`）：

- **外卖营业额**：`SUM(sale_bill.amount)` 中满足外卖条件的账单。
- **外卖实收**：`SUM(sale_bill.payment_amount)` 同上。

仓库内现成文件：

- `ttpos-scripts/bigquery/takeout_revenue_query.sql` — 单 dataset 模板。
- `ttpos-scripts/bigquery/export_takeout_revenue.py` — 多店 CSV + 可选合并 Excel Sheet1。

---

### Pattern F: 多源跨店月报（盘点 + 采购 + 调入 + 消耗）

典型需求：53 家门店 × 多月数据 × 多指标列 × 部分数据来自 ERPNext。

```python
#!/usr/bin/env python3
"""多源跨店月度库存报表
依赖: pip install google-cloud-bigquery openpyxl requests
认证: gcloud auth application-default login
"""
import argparse
from collections import defaultdict
from google.cloud import bigquery
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill

PROJECT = "diyl-407103"

def main():
    args = parse_args()
    client = bigquery.Client(project=args.project)

    # 门店列表（从参数或查询获取）
    shops = args.shops.split(",") if args.shops else query_shop_list(client)

    # 1. 从 BQ 查各维度数据（跨店）
    stocktake_feb = query_stocktake_across_shops(client, shops, args.feb_start, args.feb_end)
    stocktake_mar = query_stocktake_across_shops(client, shops, args.mar_start, args.mar_end)
    transfer_in   = query_transfer_in_across_shops(client, shops, args.mar_start, args.mar_end)
    consumption_feb = query_consumption_across_shops(client, shops, args.feb_start, args.feb_end)
    consumption_mar = query_consumption_across_shops(client, shops, args.mar_start, args.mar_end)

    # 2. 从 ERPNext 查内部采购（如有 API）
    purchase_mar = query_erpnext_purchase(args.erpnext_url, args.erpnext_token, args.mar_start, args.mar_end)

    # 3. 合并到物品维度
    merged = merge_all(stocktake_feb, stocktake_mar, transfer_in, purchase_mar,
                       consumption_feb, consumption_mar)

    # 4. 写 Excel
    write_excel(merged, args.output)
    print(f"导出完成: {args.output}")

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--project", default=PROJECT)
    p.add_argument("--shops", default="", help="逗号分隔的 dataset 列表，如 shop111,shop222")
    p.add_argument("--feb-start", default="2026-02-28", help="2月盘点日")
    p.add_argument("--feb-end", default="2026-02-28")
    p.add_argument("--mar-start", default="2026-03-01", help="3月起始")
    p.add_argument("--mar-end", default="2026-03-31", help="3月截止")
    p.add_argument("--erpnext-url", default="", help="ERPNext API base URL")
    p.add_argument("--erpnext-token", default="", help="ERPNext API token")
    p.add_argument("--output", default="monthly_report.xlsx")
    return p.parse_args()

# ---------- BigQuery 查询函数 ----------

def query_stocktake_across_shops(client, shops, date_start, date_end):
    """跨店查月盘实盘数量（基准单位）"""
    result = defaultdict(float)  # key: (material_code, material_name)
    for dataset in shops:
        sql = f"""
        SELECT
          IFNULL(m.code, '') AS material_code,
          JSON_EXTRACT_SCALAR(m.name, '$.zh') AS material_name,
          SUM(sri.counted_quantity) AS counted_qty
        FROM `{client.project}`.`{dataset}`.`ttpos_stock_reconciliation` sr
        JOIN `{client.project}`.`{dataset}`.`ttpos_stock_reconciliation_item` sri
          ON sri.stock_reconciliation_uuid = sr.uuid AND sri.delete_time = 0
        JOIN `{client.project}`.`{dataset}`.`ttpos_material` m
          ON m.uuid = sri.material_uuid AND m.delete_time = 0
        WHERE sr.delete_time = 0
          AND sr.type = 5
          AND sr.status = 2
          AND sr.submit_time BETWEEN
            UNIX_SECONDS(TIMESTAMP('{date_start}'))
            AND UNIX_SECONDS(TIMESTAMP('{date_end} 23:59:59'))
        GROUP BY material_code, material_name
        """
        for row in client.query(sql).result():
            key = (row.material_code, row.material_name)
            result[key] += float(row.counted_qty or 0)
    return result

def query_transfer_in_across_shops(client, shops, date_start, date_end):
    """跨店查调入数量（基准单位 = num * conversion_rate）"""
    result = defaultdict(float)
    for dataset in shops:
        sql = f"""
        SELECT
          IFNULL(ti.material_code, '') AS material_code,
          JSON_EXTRACT_SCALAR(ti.material_name, '$.zh') AS material_name,
          SUM(tiu.num * tiu.unit_conversion_rate) AS transfer_qty
        FROM `{client.project}`.`{dataset}`.`ttpos_transfer_order` t_ord
        JOIN `{client.project}`.`{dataset}`.`ttpos_transfer_order_item` ti
          ON ti.transfer_order_uuid = t_ord.uuid AND ti.delete_time = 0
        JOIN `{client.project}`.`{dataset}`.`ttpos_transfer_order_item_unit` tiu
          ON tiu.item_uuid = ti.uuid AND tiu.delete_time = 0
        WHERE t_ord.delete_time = 0
          AND t_ord.transfer_type = 1
          AND t_ord.status = 4
          AND t_ord.order_time BETWEEN
            UNIX_SECONDS(TIMESTAMP('{date_start}'))
            AND UNIX_SECONDS(TIMESTAMP('{date_end} 23:59:59'))
        GROUP BY material_code, material_name
        """
        for row in client.query(sql).result():
            key = (row.material_code, row.material_name)
            result[key] += float(row.transfer_qty or 0)
    return result

def query_consumption_across_shops(client, shops, date_start, date_end):
    """跨店查消耗数量（基准单位）"""
    result = defaultdict(float)
    for dataset in shops:
        sql = f"""
        SELECT
          IFNULL(m.code, '') AS material_code,
          JSON_EXTRACT_SCALAR(m.name, '$.zh') AS material_name,
          SUM(som.num) AS consumption_qty
        FROM `{client.project}`.`{dataset}`.`ttpos_sale_order_material` som
        JOIN `{client.project}`.`{dataset}`.`ttpos_sale_order` so
          ON so.uuid = som.sale_order_uuid AND so.delete_time = 0
        JOIN `{client.project}`.`{dataset}`.`ttpos_material` m
          ON m.uuid = som.material_uuid AND m.delete_time = 0
        WHERE som.delete_time = 0
          AND so.status = 1
          AND so.finish_time BETWEEN
            UNIX_SECONDS(TIMESTAMP('{date_start}'))
            AND UNIX_SECONDS(TIMESTAMP('{date_end} 23:59:59'))
        GROUP BY material_code, material_name
        """
        for row in client.query(sql).result():
            key = (row.material_code, row.material_name)
            result[key] += float(row.consumption_qty or 0)
    return result

# ---------- ERPNext 查询 ----------

def query_erpnext_purchase(base_url, token, date_start, date_end):
    """从 ERPNext API 查内部采购量"""
    if not base_url or not token:
        return {}
    import requests
    # ERPNext API: 查 Purchase Receipt 或 Stock Entry (type=Purchase Receipt)
    # 具体 API 根据实际 ERPNext 版本调整
    url = f"{base_url}/api/resource/Stock Entry"
    params = {
        "filters": f'[["stock_entry_type","=","Material Receipt"],["posting_date",">=","{date_start}"],["posting_date","<=","{date_end}"],["docstatus","=",1]]',
        "fields": '["name"]',
        "limit_page_length": 0,
    }
    headers = {"Authorization": f"token {token}"}
    resp = requests.get(url, params=params, headers=headers)
    # TODO: 根据实际 ERPNext 结构解析，返回 dict[(code, name)] = qty
    return {}

# ---------- 数据合并 ----------

def merge_all(st_feb, st_mar, transfer_in, purchase, cons_feb, cons_mar):
    """合并所有数据源到统一物品维度"""
    all_keys = set()
    for d in [st_feb, st_mar, transfer_in, purchase, cons_feb, cons_mar]:
        all_keys.update(d.keys())

    headers = [
        "物品编码", "物品名称",
        "2月月盘实盘", "3月月盘实盘",
        "3月内部采购", "3月调入数量",
        "2月消耗数量", "3月消耗数量",
    ]

    data = []
    for key in sorted(all_keys, key=lambda k: k[0]):
        code, name = key
        data.append([
            code, name,
            round(st_feb.get(key, 0), 4),
            round(st_mar.get(key, 0), 4),
            round(purchase.get(key, 0), 4),
            round(transfer_in.get(key, 0), 4),
            round(cons_feb.get(key, 0), 4),
            round(cons_mar.get(key, 0), 4),
        ])
    return {"headers": headers, "data": data}

# ---------- Excel 输出 ----------

def write_excel(result, output_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "月度库存报表"

    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font = Font(bold=True, size=11, color="FFFFFF")

    for col_idx, h in enumerate(result["headers"], 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for row_idx, row in enumerate(result["data"], 2):
        for col_idx, val in enumerate(row, 1):
            ws.cell(row=row_idx, column=col_idx, value=val)

    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

    ws.freeze_panes = "A2"
    wb.save(output_path)

if __name__ == "__main__":
    main()
```

**关键设计点**：

1. **跨店循环** — 每个维度独立循环 53 个 dataset，用 `defaultdict` 按 `(material_code, material_name)` 聚合
2. **多数据源** — BQ 查 TTPOS 数据，ERPNext API 查采购数据，最终在 Python 侧 merge
3. **基准单位** — 盘点用 `counted_quantity`（已是基准单位），调入用 `num * unit_conversion_rate`，消耗用 `som.num`
4. **ERPNext 查询** — 预留了 `query_erpnext_purchase` 函数，需根据实际 API 结构填充
5. **日期参数化** — 2 月/3 月的起止日期通过命令行参数传入，可复用于任意月份

---

## 脚本管理约定

| 项 | 约定 |
|----|------|
| 存放目录 | `ttpos-scripts/bigquery/` |
| 命名规则 | `export_{主题}_{日期}.py`，如 `export_bom_20260410.py` |
| 依赖 | 脚本头部注释写明 `pip install` 命令 |
| 认证 | 统一用 `gcloud auth application-default login` |
| 不入库 | 一次性脚本不提交 Git，通用脚本可提交 |
| 输出 | 默认输出到当前目录，文件名通过 `--output` 指定 |
