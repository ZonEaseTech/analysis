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
