# BigQuery 数据导出开发指南

## 目录

1. [快速开始](#快速开始)
2. [核心概念](#核心概念)
3. [工具类使用](#工具类使用)
4. [开发流程](#开发流程)
5. [最佳实践](#最佳实践)

---

## 快速开始

### 最简单的导出脚本

```python
#!/usr/bin/env python3
from utils.bq_exporter import MultiShopExporter

# 创建导出器
exporter = MultiShopExporter(
    project_id="diyl-407103",
    output_path="exports/my_report.xlsx"
)

# 加载商家列表
exporter.load_merchants("resources/merchants.xlsx")

# 执行导出
result = exporter.export(
    sql_template="""
        SELECT 
          c.name AS store_name,
          ROUND(SUM(sb.amount), 2) AS total_turnover
        FROM `{project}`.`{dataset}`.`ttpos_sale_bill` sb
        CROSS JOIN `{project}`.`{dataset}`.`ttpos_company` c
        WHERE sb.delete_time = 0 AND sb.status = 1
          AND sb.finish_time >= {start_ts} AND sb.finish_time < {end_ts}
          AND c.delete_time = 0
        GROUP BY c.name
        LIMIT 1
    """,
    start_ts=1772323200,
    end_ts=1775001600
)

print(f"导出完成: {result.success_count}/{result.total_count}")
```

---

## 核心概念

### 1. 项目结构

```
analysis/
├── utils/
│   ├── bq_client.py          # BigQuery 客户端配置
│   ├── bq_exporter.py        # 导出框架（核心）
│   ├── validators.py         # 校验框架
│   └── takeout_detector.py   # 外卖订单识别
├── scripts/                   # 导出脚本存放目录
└── exports/                   # 导出目录
```

### 2. 时间处理

所有时间戳使用 **Unix 秒（UTC）**：

```python
from datetime import datetime, timezone

# 日期转时间戳
def day_start_unix_utc(day: str) -> int:
    dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())

start_ts = day_start_unix_utc("2026-03-01")  # 1772323200
end_ts = day_start_unix_utc("2026-04-01")    # 1775001600
```

### 3. SQL 模板占位符

| 占位符 | 说明 | 示例 |
|--------|------|------|
| `{project}` | GCP 项目 ID | `diyl-407103` |
| `{dataset}` | 门店 dataset | `shop1958987436032000` |
| `{start_ts}` | 开始时间戳 | `1772323200` |
| `{end_ts}` | 结束时间戳 | `1775001600` |

---

## 工具类使用

### MultiShopExporter

多门店批量导出器，支持并行查询多个门店。

```python
from utils.bq_exporter import MultiShopExporter, ExcelConfig

exporter = MultiShopExporter(
    project_id="diyl-407103",
    output_path="exports/report.xlsx"
)

# 设置 Excel 配置
exporter.set_excel_config(ExcelConfig(
    sheet_name="销售报表",
    headers=["门店编号", "门店名称", "营业额", "订单数"],
    number_format="0.00"
))

# 设置进度回调
exporter.set_progress_callback(
    lambda idx, total, account, data: print(f"[{idx}/{total}] {account}")
)

# 自定义行处理器
def process_row(row, account):
    return {
        "门店名称": row.store_name,
        "营业额": float(row.total_turnover),
        "订单数": int(row.order_count)
    }

result = exporter.export(
    sql_template="...",
    start_ts=start_ts,
    end_ts=end_ts,
    row_processor=process_row
)
```

### TakeoutRevenueExporter

专门用于外卖营业额统计的导出器，预置了华莱士标准的统计逻辑。

```python
from utils.bq_exporter import TakeoutRevenueExporter

exporter = TakeoutRevenueExporter(
    output_path="exports/takeout_report.xlsx"
)

result = exporter.export_takeout_revenue(
    start_date="2026-03-01",
    end_date="2026-04-01",
    merchant_xlsx="resources/merchants.xlsx"
)

# 结果包含校验信息
if result.validation_result:
    print(f"校验通过: {result.validation_result.is_valid}")
    for error in result.validation_result.errors:
        print(f"  错误: {error.message}")
```

### 校验器

#### 内置校验器

| 校验器 | 用途 | 示例 |
|--------|------|------|
| `ConsistencyValidator` | 一致性校验 | 总=分项和 |
| `RangeValidator` | 范围校验 | 无负值、最大值 |
| `RatioValidator` | 比例校验 | 占比不超过100% |
| `CrossSourceValidator` | 跨源比对 | Excel vs BQ |

#### 创建校验链

```python
from utils.validators import (
    ValidationChain,
    ConsistencyValidator,
    RangeValidator,
    CrossSourceValidator
)

chain = ValidationChain()

# 添加一致性校验
chain.add(ConsistencyValidator(
    total_field="total_turnover",
    sum_fields=["takeout_turnover", "non_takeout_turnover"]
))

# 添加范围校验
chain.add(RangeValidator([
    {"field": "total_turnover", "min": 0},
    {"field": "takeout_turnover", "min": 0, "max": 1000000}
]))

# 执行校验
result = chain.validate(excel_data)
print(f"校验结果: {result.is_valid}")
print(f"错误数: {len(result.errors)}")
print(f"警告数: {len(result.warnings)}")
```

---

## 开发流程

### 标准开发流程

1. **明确需求**
   - 数据主题
   - 时间范围
   - 过滤条件
   - 输出格式

2. **选择基础模板**

   ```python
   # 选项1: 多门店通用导出
   from utils.bq_exporter import MultiShopExporter
   
   # 选项2: 外卖营业额导出
   from utils.bq_exporter import TakeoutRevenueExporter
   
   # 选项3: 自定义（继承 BaseExporter）
   from utils.bq_exporter import BaseExporter
   ```

3. **编写 SQL**

   ```sql
   WITH 
   -- 1. 提取原始数据
   raw_data AS (
     SELECT ...
     FROM `{project}`.`{dataset}`.`ttpos_xxx`
     WHERE delete_time = 0
       AND finish_time >= {start_ts}
       AND finish_time < {end_ts}
   ),
   
   -- 2. 聚合计算
   summary AS (
     SELECT 
       SUM(amount) AS total,
       COUNT(*) AS cnt
     FROM raw_data
   )
   
   -- 3. 最终结果
   SELECT * FROM summary
   ```

4. **添加校验规则**

   ```python
   from utils.validators import ConsistencyValidator, RangeValidator
   
   exporter.set_validators([
       ConsistencyValidator(total_field="total", sum_fields=["a", "b"]),
       RangeValidator([{"field": "amount", "min": 0}])
   ])
   ```

5. **测试验证**
   - 小数据量测试
   - 校验结果检查
   - Excel 格式确认

6. **部署运行**
   - 完整数据导出
   - 结果归档

---

## 最佳实践

### 1. SQL 优化

**避免全表扫描**：

```sql
-- ❌ 不好：可能扫描大量数据
SELECT * FROM `table` WHERE delete_time = 0

-- ✅ 好：添加时间过滤
SELECT * FROM `table` 
WHERE delete_time = 0 
  AND finish_time >= {start_ts} 
  AND finish_time < {end_ts}
```

**使用 CTE 分层**：

```sql
-- ✅ 好：逻辑清晰，便于调试
WITH 
step1 AS (SELECT ...),
step2 AS (SELECT ...)
SELECT * FROM step2
```

### 2. 错误处理

```python
result = exporter.export(...)

# 检查失败项
if result.failed_count > 0:
    for error in result.errors:
        print(f"门店 {error['account']}: {error['error']}")

# 检查校验结果
if result.validation_result and not result.validation_result.is_valid:
    for error in result.validation_result.errors:
        print(f"校验失败: {error.message}")
```

### 3. 调试技巧

**打印 SQL**：

```python
sql = sql_template.format(
    project="diyl-407103",
    dataset="shop123456",
    start_ts=1772323200,
    end_ts=1775001600
)
print(sql)  # 复制到 BQ 控制台调试
```

**单门店测试**：

```python
# 只测试第一个门店
exporter.merchants = [exporter.merchants[0]]
result = exporter.export(...)
```

### 4. 代码规范

- 脚本命名：`export_<主题>_<时间范围>.py`
- 输出路径：`exports/<主题>_<日期>.xlsx`
- 必须包含校验
- 保留原始 SQL 模板（便于复现）

---

## 常见问题

### Q: 如何处理大量数据？

A: 使用分页查询或增加时间分段：

```python
# 按月分段导出
for month in ["2026-01", "2026-02", "2026-03"]:
    start_ts = day_start_unix_utc(f"{month}-01")
    end_ts = day_start_unix_utc(f"{month+1}-01")
    exporter.export(...)
```

### Q: 校验失败怎么办？

A: 检查数据源头：

1. 对比 Excel 和 BQ 抽样数据
2. 检查 SQL 逻辑是否有遗漏
3. 确认时间范围是否一致

### Q: 如何添加新的校验规则？

A: 继承 `DataValidator` 基类：

```python
from utils.validators import DataValidator, ValidationResult

class MyValidator(DataValidator):
    @property
    def name(self) -> str:
        return "my_rule"
    
    def validate(self, data):
        # 自定义校验逻辑
        if 条件不满足:
            return ValidationResult.failure([...])
        return ValidationResult.success()
```
