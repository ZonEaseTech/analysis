---
name: bigquery-export
description: 从 BigQuery 导出 TTPOS 数据报表。生成 SQL 查询或 Python 脚本（google-cloud-bigquery + openpyxl）。当用户提到"导出"、"报表"、"BigQuery"、"BQ"、"数据导出"、"Excel 导出"、"表格导出"、"数据聚合"时触发。
triggers:
  - 导出
  - 报表
  - BigQuery
  - BQ
  - 数据导出
  - Excel 导出
  - 表格导出
  - 数据聚合
  - 外卖营业额
  - 外卖统计
---

# BigQuery 数据导出

## 快速开始

### 外卖营业额统计（华莱士标准）

```python
from utils.bq_exporter import TakeoutRevenueExporter

exporter = TakeoutRevenueExporter(
    project_id="diyl-407103",
    output_path="exports/takeout_revenue.xlsx"
)

result = exporter.export_takeout_revenue(
    start_date="2026-03-01",
    end_date="2026-04-01",
    merchant_xlsx="resources/merchants.xlsx"
)

print(f"导出完成: {result.success_count}/{result.total_count} 家")
print(f"校验结果: {'通过' if result.validation_result.is_valid else '失败'}")
```

### 通用多门店导出

```python
from utils.bq_exporter import MultiShopExporter
from utils.validators import ConsistencyValidator, RangeValidator

exporter = MultiShopExporter(
    project_id="diyl-407103",
    output_path="exports/report.xlsx"
)

# 添加校验器
exporter.set_validators([
    ConsistencyValidator(total_field="total", sum_fields=["a", "b"]),
    RangeValidator([{"field": "amount", "min": 0}])
])

# 加载商家列表
exporter.load_merchants("resources/merchants.xlsx")

# 执行导出
result = exporter.export(
    sql_template="SELECT ... FROM `{project}.{dataset}.table` ...",
    start_ts=1772323200,
    end_ts=1775001600
)
```

## 触发条件

用户需要从 BigQuery 导出 TTPOS 业务数据，包括但不限于：
- 商品/BOM/成本卡数据
- 订单/销售报表
- 库存/进出货记录
- 会员消费/充值报表
- 任何需要跨表聚合、行转列、多 Sheet 导出的场景

---

## Phase 1: 需求澄清

### 1.1 从用户描述中提取

| 信息项 | 说明 | 示例 |
|--------|------|------|
| **数据主题** | 导什么 | BOM 成分、订单明细、库存盘点 |
| **BQ 项目** | GCP project ID | `diyl-407103` |
| **门店 dataset** | `shop{company_uuid}` | `shop3087884357632000` |
| **过滤条件** | 总部/门店/时间/状态 | `headquarter_uuid = xxx`、某个月 |
| **输出格式** | CSV/Excel/行转列/拼接 | "每个物品单独一列"、"导成 Excel" |
| **语言偏好** | 多语言字段提取哪种 | 中文(zh)、泰文(th)、英文(en) |

### 1.2 信息不足时引导

```yaml
AskQuestion:
  Q1: BigQuery 项目和门店 dataset？
    hint: "格式: project.shop{company_uuid}，如 diyl-407103.shop3087884357632000"
  Q2: 需要哪些过滤条件？
    options: [总部数据(headquarter_uuid), 指定时间范围, 指定状态, 不需要过滤]
  Q3: 输出格式偏好？
    options: [纯 SQL（复制到 BQ 控制台跑）, Python 脚本（直接导出 Excel）, 先看 SQL 再决定]
```

### 1.3 决策：SQL vs Python

```
用户需求 ──→ 能用单条 SQL 解决吗？
              │
              ├─ YES → 纯 SQL 模式
              │   适用：简单聚合、固定列数行转列、字符串拼接
              │
              └─ NO → Python 脚本模式
                  适用：
                  ├─ 动态列数（每行物品数不同，要自动展开）
                  ├─ 多 Sheet 导出（按分类/门店分 Sheet）
                  ├─ 复杂后处理（计算占比、排名、条件格式）
                  ├─ 跨门店批量（循环多个 dataset 聚合）
                  ├─ **多数据源**（BQ + ERPNext API 混合查询）
                  ├─ **月度库存报表**（盘点 + 采购 + 调入 + 消耗）
                  ├─ 大数据量分批导出
                  └─ Excel 格式要求（合并单元格、表头样式、列宽）
```

---

## Phase 2: Schema 定位

读取 [schema-reference.md](schema-reference.md) 找到涉及的表和 JOIN 关系。

关键点：
- 所有表前缀 `ttpos_`
- 软删除统一用 `delete_time = 0` 过滤
- 多语言字段（name 等）存储为 JSON：`{"zh":"中文","th":"ไทย","en":"English"}`
- 时间字段为 Unix 时间戳（秒），非 datetime

---

## Phase 3: SQL 生成

读取 [query-patterns.md](query-patterns.md) 选择合适模式。

### SQL 模式规范

```sql
-- BigQuery 语法要点：
-- 1. 表引用：`project`.`dataset`.`ttpos_table_name`
-- 2. JSON 提取：JSON_EXTRACT_SCALAR(field, '$.zh')
-- 3. 字符串聚合：STRING_AGG(expr, delimiter ORDER BY col)
-- 4. 类型转换：CAST(num AS STRING)（不是 CONVERT）
-- 5. 时间转换：TIMESTAMP_SECONDS(unix_ts)
-- 6. 条件聚合：MAX(IF(condition, value, NULL))（不是 CASE WHEN 也行）
-- 7. 列名限制：只能用英文、数字、下划线（不支持中文列名）
-- 8. NULL 处理：IFNULL(expr, default)（不是 ISNULL）
```

### 通用模板

```sql
SELECT
  JSON_EXTRACT_SCALAR(t.name, '$.zh') AS name_zh,
  -- ...其他字段
FROM `{project}`.`{dataset}`.`ttpos_{table}` AS t
WHERE t.delete_time = 0
  -- ...其他过滤
ORDER BY t.uuid;
```

---

## Phase 4: Python 脚本生成

当决策为 Python 模式时，生成独立可执行的 Python 脚本。

### 脚本结构

```python
#!/usr/bin/env python3
"""
{报表描述}
用法: python export_{name}.py [--project PROJECT] [--dataset DATASET] [--output OUTPUT]
依赖: pip install google-cloud-bigquery openpyxl
"""
import argparse
from google.cloud import bigquery
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill

def main():
    args = parse_args()
    client = bigquery.Client(project=args.project)

    # 1. 查询数据
    rows = query_data(client, args.dataset)

    # 2. 处理/聚合
    processed = process_data(rows)

    # 3. 写入 Excel
    write_excel(processed, args.output)
    print(f"导出完成: {args.output}")

def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="diyl-407103")
    parser.add_argument("--dataset", required=True, help="shop{company_uuid}")
    parser.add_argument("--output", default="export.xlsx")
    return parser.parse_args()

def query_data(client, dataset):
    sql = f"""
    SELECT ...
    FROM `{client.project}`.`{dataset}`.`ttpos_xxx` AS t
    WHERE t.delete_time = 0
    """
    return list(client.query(sql).result())

def process_data(rows):
    # 行转列、聚合、分组等复杂逻辑
    ...

def write_excel(data, output_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    # 表头样式
    header_font = Font(bold=True, size=11)
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font_white = Font(bold=True, size=11, color="FFFFFF")

    # 写入表头
    headers = ["列1", "列2", "列3"]
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font_white
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    # 写入数据
    for row_idx, row_data in enumerate(data, 2):
        for col_idx, value in enumerate(row_data, 1):
            ws.cell(row=row_idx, column=col_idx, value=value)

    # 自动列宽
    for col in ws.columns:
        max_length = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_length + 4, 50)

    wb.save(output_path)

if __name__ == "__main__":
    main()
```

### Python 脚本规范

1. **独立可执行** — 不依赖项目代码，复制走就能跑
2. **参数化** — project/dataset/output 都通过 argparse 传入
3. **GCP 认证** — 依赖 `GOOGLE_APPLICATION_CREDENTIALS` 环境变量或 `gcloud auth application-default login`
4. **Excel 样式** — 默认带表头样式、自动列宽、冻结首行
5. **脚本放置** — 生成到 `ttpos-scripts/bigquery/` 目录下

---

## 专项：外卖营业额统计

### 统计口径（华莱士标准）

外卖订单判定（满足**任一条件**即计入，自动去重）：

| 条件 | 判定逻辑 | 数据源 |
|------|----------|--------|
| **条件1** | 支付方式匹配 `Robinhood/Grab/Lineman/Shopee`（不区分大小写/空格） | `ttpos_payment_order` |
| **条件2** | 订单来源渠道匹配 `Grab/LINE MAN`（多语言名称或JSON快照） | `ttpos_order_source` |
| **条件3** | 账单类型为会员外送 `bill_type = 2` | `ttpos_sale_bill` |
| **外加** | 外卖平台已完成订单 `platform IN ('grab', 'lineman', 'shopee')` | `ttpos_takeout_order` |

### 使用工具类

```python
from utils.takeout_detector import TakeoutOrderDetector

# 使用默认配置
detector = TakeoutOrderDetector.default()

# 生成 SQL 条件片段
sql_condition = detector.build_payment_condition("po")
# 结果: REGEXP_CONTAINS(LOWER(REPLACE(po.payment_method_name, ' ', '')), r'robinhood|grab|lineman|shopee')

# 获取完整 SQL 模板
sql_template = detector.get_sql_template()
```

### 交叉校验机制

导出报表**必须**执行以下校验：

```python
from utils.validators import (
    ValidationChain, 
    ConsistencyValidator, 
    RangeValidator,
    RatioValidator,
    CrossSourceValidator
)

# 创建校验链
validators = ValidationChain()

# 1. 内部一致性校验
validators.add(ConsistencyValidator(
    total_field='total_turnover',
    sum_fields=['takeout_turnover', 'non_takeout_turnover']
))

# 2. 数值范围校验
validators.add(RangeValidator([
    {"field": "total_turnover", "min": 0, "name": "总营业额非负"},
    {"field": "takeout_turnover", "min": 0, "name": "外卖营业额非负"},
]))

# 3. 比例校验（外卖占比不超过100%）
validators.add(RatioValidator(
    parent_field='total_turnover',
    child_field='takeout_turnover',
    max_ratio=1.0
))

# 4. 跨源比对校验（抽样）
validators.add(CrossSourceValidator(
    bq_client=client,
    sample_count=5,
    compare_field='total_turnover'
))

# 执行校验
result = validators.validate(excel_data)
if not result.is_valid:
    print("校验失败:", result.errors)
```

### 快捷校验函数

```python
from utils.validators import create_default_validators

# 一键创建外卖营业额报表的标准校验链
validators = create_default_validators(
    total_field="total_turnover",
    takeout_field="takeout_turnover",
    non_takeout_field="non_takeout_turnover"
)
```

## Phase 5: 迭代

根据用户反馈：
- 调整列/字段
- 修改过滤条件
- 切换输出格式（SQL ↔ Python）
- 增加 Sheet 或分组维度

---

## 常见陷阱

| 陷阱 | 说明 | 解法 |
|------|------|------|
| 中文列名 | BigQuery 不支持中文列别名 | 用英文别名，Excel 里再改表头 |
| 多语言 JSON | name 字段是 JSON 不是纯文本 | `JSON_EXTRACT_SCALAR(name, '$.zh')` |
| 软删除 | 每张表都有 delete_time | 所有 JOIN 和 WHERE 都加 `delete_time = 0` |
| 时间戳 | 存的是 Unix 秒，不是 datetime | `TIMESTAMP_SECONDS(ts)` 或 Python 里 `datetime.fromtimestamp()` |
| 多租户 dataset | 每个门店独立 dataset | 必须指定 `shop{uuid}`，跨店要 UNION |
| NULL 编码 | m.code 可能为空 | `IFNULL(m.code, '')` |
| GROUP BY | BQ 严格模式，SELECT 非聚合列必须在 GROUP BY 中 | 确保一致 |
| 盘点日期 | submit_time 是提交时间，不是盘点日期 | 按 submit_time 范围过滤，注意选对日期 |
| 调入单位换算 | 调拨数量需乘 unit_conversion_rate 转基准单位 | `SUM(num * unit_conversion_rate)` |
| 跨店物品对齐 | 不同门店可能有不同物品 | 用 material code 做 key 合并，缺失填 0 |
| ERPNext 数据 | 采购数据不在 BQ 中 | 通过 ERPNext API 单独查询，Python 侧 merge |
