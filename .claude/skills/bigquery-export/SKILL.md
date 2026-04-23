---
name: bigquery-export
description: TTPOS BigQuery 数据导出入口。识别用户意图并路由到子文档：表结构速查、SQL 模式、外卖报表、利润报表、编码匹配。
triggers:
  - BigQuery
  - BQ
  - ttpos_
  - 数据导出
---

# BigQuery 数据导出入口

## 快速路由

| 用户意图 | 目标文档 |
|----------|----------|
| 查表结构 / 字段含义 / JOIN 关系 | [schema-reference.md](schema-reference.md) |
| 写 SQL / 行转列 / 时间处理 / 字符串聚合 | [query-patterns.md](query-patterns.md) |
| 外卖营业额 / 外卖统计 / 平台订单 | [takeout-report.md](takeout-report.md) |
| 利润 / 成本 / 毛利率 / BOM 成本 | [profit-margin.md](profit-margin.md) |
| 已有 Excel 匹配 BQ 编码 / 补编码 | [excel-matching.md](excel-matching.md) |
| 不确定意图 | 走下方 Phase 1 需求澄清流程 |

---

## Phase 1: 需求澄清

### 信息提取

| 信息项 | 说明 | 示例 |
|--------|------|------|
| 数据主题 | 导什么 | BOM 成分、订单明细、库存盘点 |
| BQ 项目 | GCP project ID | `diyl-407103` |
| 门店 dataset | `shop{company_uuid}` | `shop3087884357632000` |
| 时间范围 | **必须指定**（硬规则） | `2026-03-01` 至 `2026-04-01` |
| 过滤条件 | 总部/门店/状态 | `headquarter_uuid = xxx` |
| 输出格式 | CSV/Excel/行转列/拼接 | "每个物品单独一列" |
| 语言偏好 | 多语言字段提取 | 中文(zh)、泰文(th)、英文(en) |

### SQL vs Python 决策

```
用户需求 ──→ 能用单条 SQL 解决吗？
              │
              ├─ YES → 纯 SQL 模式
              │   适用：简单聚合、固定列数行转列、字符串拼接
              │
              └─ NO → Python 脚本模式
                  适用：动态列数、多 Sheet、复杂后处理、跨门店批量、
                       多数据源（BQ + ERPNext）、大数据量分批、Excel 格式
```

---

## Phase 2: Schema 定位

读取 [schema-reference.md](schema-reference.md) 找到涉及的表和 JOIN 关系。

关键点：
- 所有表前缀 `ttpos_`
- 软删除统一用 `delete_time = 0`
- 多语言字段（name 等）存储为 JSON：`{"zh":"中文","th":"ไทย","en":"English"}`
- 时间字段为 Unix 时间戳（秒）

---

## Phase 3: SQL 生成

读取 [query-patterns.md](query-patterns.md) 选择合适模式。

### SQL 语法速查

```sql
-- 表引用：`project`.`dataset`.`ttpos_table_name`
-- JSON 提取：JSON_EXTRACT_SCALAR(field, '$.zh')
-- 字符串聚合：STRING_AGG(expr, ', ' ORDER BY col)
-- 时间转换：TIMESTAMP_SECONDS(unix_ts)
-- 条件聚合：MAX(IF(condition, value, NULL))
-- 列名限制：只能用英文、数字、下划线
-- NULL 处理：IFNULL(expr, default)
```

---

## Phase 4: Python 脚本

当决策为 Python 模式时，生成独立可执行脚本。

> **所有脚本必须在开头调用 `setup_proxy()`，否则连接 BigQuery 会 600 秒超时。**

### 脚本规范

1. **独立可执行** — 复制走就能跑
2. **参数化** — project/dataset/output 通过 argparse 传入
3. **时间范围必传** — 禁止无时间过滤的全量导出
4. **GCP 认证** — `GOOGLE_APPLICATION_CREDENTIALS` 或 `gcloud auth application-default login`
5. **Excel 样式** — 表头样式、自动列宽、冻结首行
6. **脚本放置** — `ttpos-scripts/bigquery/` 目录下

脚本模板见 [query-patterns.md](query-patterns.md) Python 模式部分。

---

## Phase 5: 迭代

根据用户反馈调整列/字段、过滤条件、输出格式或增加 Sheet。

---

## 项目硬规则

1. **虚拟环境**：`venv/bin/python`，禁止直接用系统 `python3`
2. **代理设置**：`setup_proxy()` → `http://127.0.0.1:7897`
3. **时间范围必传**：所有导出报表必须有 `start_date`/`end_date`
4. **软删除过滤**：所有 JOIN 和 WHERE 加 `delete_time = 0`
5. **多语言提取**：`JSON_EXTRACT_SCALAR(name, '$.zh')`，勿直接当文本

---

## 高频陷阱

| 陷阱 | 后果 | 解法 |
|------|------|------|
| 忘记设代理 | `ConnectTimeoutError` 600s 超时 | 脚本开头调 `setup_proxy()` |
| JSON 字段当文本 | 返回 `"{"zh":"..."}"` 而非中文 | 用 `JSON_EXTRACT_SCALAR` |
| 中文列别名 | BQ 语法错误 | 英文别名，Excel 里改表头 |
| 时间戳当 datetime | 过滤条件失效 | `TIMESTAMP_SECONDS(ts)` 转换 |
| GROUP BY 漏列 | BQ 严格模式报错 | SELECT 非聚合列必须进 GROUP BY |
| NULL 编码 | 匹配失败 | `IFNULL(m.code, '')` |
| JOIN 膨胀 | 行数指数级增长，传输量爆炸 | 拆分查询，详情见 [profit-margin.md](profit-margin.md) |
| 合并单元格写公式 | 只有左上角生效 | 只在合并区域左上角写公式 |
