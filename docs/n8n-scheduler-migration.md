# n8n-scheduler 脚本迁移指南

本文档说明如何将 `../ttpos-n8n-scheduler/scripts/` 中的 MySQL 报表脚本迁移为 BigQuery 版本。

---

## 脚本对照表

| 原脚本 (MySQL) | 新脚本 (BigQuery) | 功能 |
|---------------|------------------|------|
| `report.sh` | `scripts/report_sales_consumption_bq.py` | 销售业绩 + 物品消耗 |
| `report_bom_sales.sh` | `scripts/report_bom_sales_bq.py` | BOM商品销量（区分已设/未设BOM） |
| `report_item_consumption_statistics.sh` | `scripts/report_material_stats_bq.py` | 原料经营明细（消耗/销售/采购） |
| `report_wallace_daily_item_sales.sh` | `scripts/report_daily_sales_bq.py` | 单品日销量（堂食+外卖） |
| `report_store_feb_mar.sh` | ⏳ 待实现 | 多月库存数据（动态列） |

---

## 快速开始

### 1. 环境准备

```bash
# 确保已安装依赖
pip install google-cloud-bigquery openpyxl

# 配置 GCP 认证
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/gcp-key.json
```

### 2. 准备商家列表

创建 `resources/merchants.xlsx`，格式：

| 序号 | 账号 | UUID |
|-----|------|------|
| 1 | store001 | abc123... |
| 2 | store002 | def456... |

### 3. 运行报表

```bash
# 销售业绩 + 物品消耗
python scripts/report_sales_consumption_bq.py \
    --month 2026-01 \
    --output exports/sales_2026_01.xlsx

# BOM商品销量
python scripts/report_bom_sales_bq.py \
    --month 2026-01 \
    --output exports/bom_sales_2026_01.xlsx

# 原料经营明细（指定原料codes）
python scripts/report_material_stats_bq.py \
    --month 2026-01 \
    --materials flour,popcorn_chicken,whole_chicken \
    --output exports/material_stats_2026_01.xlsx

# 单品日销量
python scripts/report_daily_sales_bq.py \
    --month 2026-01 \
    --output exports/daily_sales_2026_01.xlsx
```

---

## MySQL vs BigQuery 差异

### 时间戳处理

| 方面 | MySQL | BigQuery |
|-----|-------|----------|
| 存储格式 | Unix timestamp | Unix timestamp (兼容) |
| 日期转换 | `FROM_UNIXTIME()` | `TIMESTAMP_SECONDS()` |
| 时区处理 | 会话时区 | 显式时区参数 |

**示例：**
```sql
-- MySQL
FROM_UNIXTIME(finish_time)  -- 依赖会话时区

-- BigQuery
TIMESTAMP_SECONDS(finish_time)  -- UTC
DATE(TIMESTAMP_SECONDS(finish_time), 'Asia/Bangkok')  -- 指定时区
```

### JSON 字段处理

| 方面 | MySQL | BigQuery |
|-----|-------|----------|
| 提取语法 | `JSON_EXTRACT(col, '$.key')` | `JSON_EXTRACT_SCALAR(col, '$.key')` |
| 返回类型 | JSON 类型 | 字符串 |

**示例：**
```sql
-- MySQL
JSON_EXTRACT(product_name, '$.zh')

-- BigQuery
JSON_EXTRACT_SCALAR(product_name, '$.zh')
```

### 表名引用

| 方面 | MySQL | BigQuery |
|-----|-------|----------|
| 格式 | `database.table` | `project.dataset.table` |
| 引用方式 | 反引号/无需 | 反引号推荐 |

**示例：**
```sql
-- MySQL
SELECT * FROM ttpos_sale_bill WHERE ...

-- BigQuery
SELECT * FROM `project`.`shop{uuid}`.`ttpos_sale_bill` WHERE ...
```

### UNION 语法

```sql
-- MySQL: 允许不同列数（需兼容）
UNION

-- BigQuery: 要求列数一致
UNION ALL  -- 性能更好，不去重
```

---

## SQL 模板说明

所有预置 SQL 模板位于 `utils/sql_templates.py`：

| 模板名称 | 用途 |
|---------|------|
| `sales_revenue` | 基础销售业绩 |
| `comprehensive_sales` | POS + 外卖综合业绩 |
| `material_consumption` | 物品消耗明细 |
| `bom_product_sales` | BOM商品销量 |
| `daily_item_sales` | 单品日销量 |
| `purchase_in` | 采购入库 |
| `material_related_sales` | 原料涉及的销售金额 |

**使用示例：**
```python
from utils.sql_templates import get_template

sql = get_template('material_consumption')
# 替换占位符
query = sql.format(
    project='diyl-407103',
    dataset='shopabc123',
    start_ts=1772323200,
    end_ts=1775001600
)
```

---

## 数据源映射

### 原脚本使用的 MySQL 表

| MySQL 表 | BigQuery 对应 | 说明 |
|---------|--------------|------|
| `ttpos_statistics_sale` | `ttpos_sale_bill` | 销售账单 |
| `ttpos_statistics_member` | 计算字段 | 会员统计 |
| `ttpos_sale_order_product` | `ttpos_sale_order_product` | 销售商品明细 |
| `ttpos_sale_order_material` | `ttpos_sale_order_material` | 销售物料消耗 |
| `ttpos_takeout_order` | `ttpos_takeout_order` | 外卖订单 |
| `ttpos_takeout_order_item` | `ttpos_takeout_order_item` | 外卖商品明细 |
| `ttpos_takeout_order_material` | `ttpos_takeout_order_material` | 外卖物料消耗 |
| `ttpos_product_package` | `ttpos_product_package` | 商品套餐 |
| `ttpos_product_bom` | `ttpos_product_bom` | BOM配置 |
| `ttpos_related_material` | `ttpos_related_material` | 关联物料 |
| `ttpos_material` | `ttpos_material` | 原料主数据 |
| `ttpos_warehouse_in_out_log` | `ttpos_warehouse_in_out_log` | 出入库记录 |

---

## 已知限制

1. **时区处理**: BigQuery 使用显式时区，原 MySQL 脚本依赖 per-store 时区配置
2. **性能**: 53 次串行查询可能需要较长时间，建议优化为并行或使用分批处理
3. **数据一致性**: BigQuery 数据有同步延迟，T+1 的数据更准确

---

## 后续优化方向

1. **动态多月报表**: `report_store_feb_mar.sh` 需要动态列生成能力
2. **增量导出**: 支持按日期范围增量导出
3. **飞书集成**: 自动发送到飞书
4. **并行查询**: 使用多线程/异步加速 53 家门店查询
