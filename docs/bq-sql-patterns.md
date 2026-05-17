# BigQuery SQL 速查卡

> 来源: `.claude/skills/bigquery-export/query-patterns.md` 迁移归档, 仅保留 SQL 语法部分.
> 用于 adhoc 写 SQL 时快速查语法, 不用反复试错.
> Python 报表开发请用 `bq_reports/` + `utils/report_engine.py` 体系, 不要按此处的旧模板写.

---

## JSON 多语言提取

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

---

## 时间戳转换

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

---

## 字符串聚合（GROUP_CONCAT 等价）

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

---

## 固定列数行转列（Pivot）

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

---

## 条件聚合（分类汇总）

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

---

## 跨门店 UNION

```sql
SELECT 'shop_A' AS shop, t.* FROM `{p}`.`shop{uuid_a}`.`ttpos_material` t WHERE t.delete_time = 0
UNION ALL
SELECT 'shop_B' AS shop, t.* FROM `{p}`.`shop{uuid_b}`.`ttpos_material` t WHERE t.delete_time = 0
```

---

## 窗口函数（排名/占比）

```sql
SELECT
  name,
  amount,
  RANK() OVER (ORDER BY amount DESC) AS rank,
  ROUND(amount / SUM(amount) OVER () * 100, 2) AS pct
FROM ...
```
