# 华莱士外卖营业额计算口径

## 版本历史

| 版本 | 日期 | 修改内容 | 作者 |
|------|------|----------|------|
| 1.0 | 2026-04-10 | 初始版本，基于 bash 脚本 payment.sh 和 BQ 查询验证 | - |

---

## 概述

本文档定义华莱士门店**外卖营业额**的统计口径，用于统一报表计算逻辑。

---

## 外卖订单判定标准

外卖订单需**满足以下任一条件**（同一订单满足多个条件时去重）：

### 条件1：支付方式匹配

| 项目 | 说明 |
|------|------|
| 匹配字段 | `ttpos_payment_order.payment_method_name` |
| 匹配规则 | 不区分大小写、去除空格后匹配 |
| 匹配值 | `Robinhood`, `Grab`, `Lineman`, `Shopee` |

**SQL 实现**：

```sql
REGEXP_CONTAINS(
  LOWER(REPLACE(po.payment_method_name, ' ', '')), 
  r'robinhood|grab|lineman|shopee'
)
```

### 条件2：订单来源渠道匹配

| 项目 | 说明 |
|------|------|
| 匹配字段 | `ttpos_order_source` 多语言名称 / `sale_bill.order_source_name` JSON 快照 |
| 匹配规则 | 多语言名称（zh/th/en）合并后匹配 |
| 匹配值 | `Grab`, `LINE MAN`, `Lineman` |

**SQL 实现**：

```sql
-- 多语言名称匹配
EXISTS (
  SELECT 1 FROM `ttpos_order_source` os
  LEFT JOIN `ttpos_multi_language_name` mln ON mln.uuid = os.multi_language_name_uuid
  WHERE os.uuid = fb.order_source_uuid
    AND REGEXP_CONTAINS(
      LOWER(REPLACE(CONCAT(IFNULL(mln.zh_name,''),IFNULL(mln.th_name,''),IFNULL(mln.en_name,'')), ' ', '')),
      r'grab|lineman'
    )
)

-- JSON 快照匹配
OR REGEXP_CONTAINS(
  LOWER(REPLACE(CONCAT(
    IFNULL(JSON_EXTRACT_SCALAR(fb.order_source_name, '$.zh'), ''),
    IFNULL(JSON_EXTRACT_SCALAR(fb.order_source_name, '$.th'), ''),
    IFNULL(JSON_EXTRACT_SCALAR(fb.order_source_name, '$.en'), '')
  ), ' ', '')),
  r'grab|lineman'
)
```

### 条件3：账单类型匹配

| 项目 | 说明 |
|------|------|
| 匹配字段 | `ttpos_sale_bill.bill_type` |
| 匹配值 | `2` (会员外送) |

**SQL 实现**：

```sql
fb.bill_type = 2
```

### 附加：外卖平台订单

外卖平台（Grab/Lineman/Shopee）已完成订单：

| 项目 | 说明 |
|------|------|
| 数据源 | `ttpos_takeout_order` |
| 状态 | `order_state = 40` (已完成) |
| 平台 | `platform IN ('grab', 'lineman', 'shopee')` |

**SQL 实现**：

```sql
SELECT SUM(subtotal) as platform_turnover
FROM `ttpos_takeout_order`
WHERE delete_time = 0 AND order_state = 40
  AND platform IN ('grab', 'lineman', 'shopee')
  AND completed_time >= {start_ts} AND completed_time < {end_ts}
```

---

## 营业额计算公式

### 外卖营业额

```
外卖营业额 = POS侧外卖订单金额 + 外卖平台订单金额

其中：
- POS侧外卖订单 = sale_bill 中满足条件1或条件2或条件3的订单金额之和
- 外卖平台订单 = takeout_order 中 platform 为 grab/lineman/shopee 的订单金额
```

### 非外卖营业额

```
非外卖营业额 = 总营业额 - 外卖营业额
```

### 实收金额

```
外卖实收金额 = POS侧外卖实收 + 外卖平台实收
非外卖实收金额 = 总实收金额 - 外卖实收金额
```

---

## 数据表关联

```
外卖营业额计算涉及表：

┌─────────────────────┐
│ ttpos_sale_bill     │ ← 主表，取 amount/payment_amount
│   - bill_type       │
│   - order_source_*  │ ← 关联条件2
└──────────┬──────────┘
           │
           ├─────────────┐
           │             │
    ┌──────▼──────┐ ┌────▼──────────────┐
    │ ttpos_sale  │ │ ttpos_order_source│ ← 条件2
    │ _order      │ └───────────────────┘
    └──────┬──────┘
           │
    ┌──────▼─────────────────┐
    │ ttpos_payment_order    │ ← 条件1
    │   - payment_method_name│
    └────────────────────────┘

┌─────────────────────┐
│ ttpos_takeout_order │ ← 外卖平台订单
│   - subtotal        │
│   - platform_total  │
└─────────────────────┘
```

---

## 校验规则

导出外卖营业额报表时，必须执行以下校验：

### 1. 内部一致性校验

```
总营业额 = 外卖营业额 + 非外卖营业额
总实收金额 = 外卖实收金额 + 非外卖实收金额
```

允许误差：≤ 0.01

### 2. 数值范围校验

- 总营业额 ≥ 0
- 外卖营业额 ≥ 0
- 非外卖营业额 ≥ 0
- 外卖营业额 ≤ 总营业额

### 3. 跨源比对校验

随机抽样 5 家门店，对比 Excel 数据与 BigQuery 原始数据：

```
|Excel总营业额 - BQ总营业额| ≤ 1.0
```

---

## 代码实现

### Python 工具类

```python
from utils.takeout_detector import TakeoutOrderDetector

# 使用标准配置
detector = TakeoutOrderDetector.default()

# 生成完整 SQL
sql = detector.get_sql_template()
```

### 完整导出脚本

```python
from utils.bq_exporter import TakeoutRevenueExporter

exporter = TakeoutRevenueExporter(
    output_path="exports/takeout_revenue.xlsx"
)

result = exporter.export_takeout_revenue(
    start_date="2026-03-01",
    end_date="2026-04-01",
    merchant_xlsx="resources/merchants.xlsx"
)

# 自动执行校验
assert result.validation_result.is_valid
```

---

## 附录：SQL 完整模板

```sql
WITH 
-- 1. POS侧已完成账单
finished_bills AS (
  SELECT
    sb.uuid AS bill_uuid,
    sb.amount AS bill_amount,
    sb.payment_amount AS bill_payment_amount,
    sb.bill_type,
    sb.order_source_uuid,
    sb.order_source_name
  FROM `{project}`.`{dataset}`.`ttpos_sale_bill` AS sb
  WHERE sb.delete_time = 0
    AND sb.status = 1
    AND sb.finish_time >= {start_ts}
    AND sb.finish_time < {end_ts}
),

-- 2. Grab/Lineman渠道的order_source
grab_lineman_sources AS (
  SELECT os.uuid AS source_uuid
  FROM `{project}`.`{dataset}`.`ttpos_order_source` AS os
  LEFT JOIN `{project}`.`{dataset}`.`ttpos_multi_language_name` AS mln
    ON mln.uuid = os.multi_language_name_uuid AND mln.delete_time = 0
  WHERE os.delete_time = 0
    AND REGEXP_CONTAINS(
      LOWER(REPLACE(CONCAT(IFNULL(mln.zh_name,''),IFNULL(mln.th_name,''),IFNULL(mln.en_name,'')), ' ', '')),
      r'grab|lineman'
    )
),

-- 3. 标记外卖订单（3个条件任一即计入）
bill_takeout AS (
  SELECT
    fb.bill_uuid,
    fb.bill_amount,
    fb.bill_payment_amount,
    (
      -- 条件1: 支付方式
      EXISTS (
        SELECT 1 FROM `{project}`.`{dataset}`.`ttpos_payment_order` po
        JOIN `{project}`.`{dataset}`.`ttpos_sale_order` so 
          ON so.uuid = po.related_uuid AND po.related_type = 0 AND so.delete_time = 0
        WHERE so.sale_bill_uuid = fb.bill_uuid AND po.delete_time = 0 AND po.status = 1
          AND REGEXP_CONTAINS(LOWER(REPLACE(po.payment_method_name, ' ', '')), r'robinhood|grab|lineman|shopee')
      )
      -- 条件2: 订单来源
      OR (fb.order_source_uuid > 0 AND fb.order_source_uuid IN (SELECT source_uuid FROM grab_lineman_sources))
      OR REGEXP_CONTAINS(LOWER(REPLACE(CONCAT(
          IFNULL(JSON_EXTRACT_SCALAR(fb.order_source_name, '$.zh'), ''),
          IFNULL(JSON_EXTRACT_SCALAR(fb.order_source_name, '$.th'), ''),
          IFNULL(JSON_EXTRACT_SCALAR(fb.order_source_name, '$.en'), '')), ' ', '')), r'grab|lineman')
      -- 条件3: 账单类型
      OR fb.bill_type = 2
    ) AS is_takeout
  FROM finished_bills fb
),

-- 4. POS侧汇总
pos_summary AS (
  SELECT
    ROUND(SUM(bill_amount), 2) AS pos_turnover,
    ROUND(SUM(bill_payment_amount), 2) AS pos_received,
    ROUND(SUM(IF(is_takeout, bill_amount, 0)), 2) AS pos_takeout_turnover,
    ROUND(SUM(IF(is_takeout, bill_payment_amount, 0)), 2) AS pos_takeout_received
  FROM bill_takeout
),

-- 5. 外卖平台订单汇总
takeout_summary AS (
  SELECT
    ROUND(SUM(subtotal), 2) AS platform_turnover,
    ROUND(SUM(platform_total), 2) AS platform_received
  FROM `{project}`.`{dataset}`.`ttpos_takeout_order`
  WHERE delete_time = 0 AND order_state = 40
    AND platform IN ('grab', 'lineman', 'shopee')
    AND completed_time >= {start_ts} AND completed_time < {end_ts}
)

-- 6. 最终结果
SELECT
  c.name AS store_name,
  IFNULL(ps.pos_turnover, 0) AS total_turnover,
  IFNULL(ps.pos_received, 0) AS total_received,
  IFNULL(ps.pos_takeout_turnover, 0) + IFNULL(ts.platform_turnover, 0) AS takeout_turnover,
  IFNULL(ps.pos_takeout_received, 0) + IFNULL(ts.platform_received, 0) AS takeout_received
FROM pos_summary ps
CROSS JOIN takeout_summary ts
CROSS JOIN `{project}`.`{dataset}`.`ttpos_company` c
WHERE c.delete_time = 0
LIMIT 1
```
