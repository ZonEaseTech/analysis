# Skill: bigquery-export (已归档)

> **本 skill 内容已合并迁移**. 不再作为独立入口使用, 保留此文件仅作向后兼容路由.
>
> 接 adhoc 导表任务 → 请走 `.claude/skills/adhoc-export/SKILL.md` (唯一入口).

---

## 迁移后的内容位置

| 原内容 | 新位置 | 说明 |
|--------|--------|------|
| Schema 速查 (表结构 / JOIN 关系) | `docs/bq-schema-reference.md` | 数据库视角参考手册 |
| SQL 语法速查 (JSON/时间戳/行转列等) | `docs/bq-sql-patterns.md` | adhoc 写 SQL 时查语法 |
| Excel↔BQ 编码匹配原则 | 并入 `adhoc-export/SKILL.md` 步骤 2 子流程 | 一次性匹配任务规范 |
| 利润报表实现思路 | ❌ 废弃 | 已被 `bq_reports/` + `semantic/` 实际架构取代 |
| 外卖报表实现思路 | ❌ 废弃 | 已被 `semantic/entities/` CTE 工厂取代 |
| Python 脚本模板 | ❌ 废弃 | 已被 `utils/report_engine.py` 配置驱动体系取代 |

---

## 为什么归档

`bigquery-export` 是项目早期的技术参考 skill, 内容分为两类:

1. **仍有价值的参考内容** (Schema 速查 / SQL 语法) → 迁移到 `docs/`, 作为纯文档使用.
2. **严重过时的实现内容** (Python 脚本模板 / 利润报表思路 / 外卖报表思路) → 废弃.
   - 实际项目已演进为 `semantic/` 语义层 + `bq_reports/` 配置驱动报表 + `utils/report_engine.py` 引擎.
   - 旧模板跟当前架构差距太大, 保留会误导 AI.

---

## 快速路由

| 用户意图 | 去这里 |
|----------|--------|
| 临时导表 / 老板要看数据 / 重新导报表 | `.claude/skills/adhoc-export/SKILL.md` |
| 客户给了新 Excel/CSV 事实表 | `.claude/skills/onboard-fact-table/SKILL.md` |
| 查 BQ 表结构 / 字段含义 | `docs/bq-schema-reference.md` |
| 查 SQL 语法 (JSON 提取 / 时间戳 / 行转列) | `docs/bq-sql-patterns.md` |
