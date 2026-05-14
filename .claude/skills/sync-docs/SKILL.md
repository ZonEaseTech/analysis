---
name: sync-docs
description: 改完代码后, 自动检查 docs/ 里哪些章节跟代码漂移, 同步更新。让 AI 维护文档跟代码一致, 替代手写 lint/generator。
triggers:
  - 同步文档
  - 改完代码
  - 文档过期
  - 改了 entity
  - 改了 aggregation
  - 改了报表脚本
  - commit 前
  - 改了 ttpos 字段使用
---

# 同步文档跟代码

## 适用场景

刚改完以下任一类文件，**commit 之前**调用本 skill：

- `semantic/entities/*.py` — 业务实体 (CTE 工厂)
- `semantic/aggregations/*.py` — 聚合层
- `semantic/validators/*.py` — 校验层
- `semantic/resolvers/*.py` — 多源裁决
- `semantic/reconciliation/*.py` — 对账层
- `semantic/analytics/*.py` — 分析层
- `bq_reports/*.py` — 报表脚本
- `resources/*.yaml` — 配置 / mapping
- `utils/resource_adapter.py` — adapter

## 流程

### 步骤 1: 看 git diff

```bash
git diff HEAD                 # uncommitted 改动
git diff --cached             # staged 改动
git log -1 --stat             # 已 commit 但未 push
```

总结改了什么：哪个文件、哪个函数 / 字段 / 公式。

### 步骤 2: 影响分析 — 对照每份文档扫一遍

| 文档 | 关注什么改动 |
|---|---|
| `docs/metrics-catalog.md` | 指标公式 / SQL 文件:行号 / 字段语义 改了 |
| `docs/data-menu.md` | "能给"/"不能给"边界 改了 / 加新维度 / 加新指标 |
| `docs/data-contract.md` | ttpos 字段使用 / 我们语义字段定义 改了 |
| `docs/ttpos-bq-field-pitfalls.md` | 发现新的 ttpos 字段陷阱 |
| `docs/profit-report-takeout-semantics.md` | 外卖口径 改了 |
| `docs/profit-margin-reconciliation-checklist.md` | 对账因素 / 排障路径 改了 |
| `docs/pnl-statement-design.md` | P&L 设计 改了 |
| `docs/pnl-accounting-standards-gap.md` | 会计准则相关 改了 |
| `docs/architecture-evolution-roadmap.md` | 整体阶段进度 改了 |
| `CLAUDE.md` / 各 README | 跑法 / 项目结构 改了 |

### 步骤 3: 列 candidate 给用户确认

输出格式（强烈建议）：

```
== Doc Sync Candidates ==

[high confidence — AI 直接改, 用户 review diff]
  docs/metrics-catalog.md:178
    - 旧: "公式 = sale_line.py:42"
    - 新: "公式 = sale_line.py:45"  (你刚把 actual_amount 计算移到 line 45)

[medium confidence — 让用户决定]
  docs/data-menu.md "维度菜单"
    - 你刚加了 sale_event.category 字段, 是否补 "按品类" 行?

[low confidence — 仅提示]
  docs/data-contract.md "ttpos_statistics_product 字段使用"
    - 你 SELECT 了 sp.member_uuid, 文档里没列, 要补吗?
```

### 步骤 4: 改 markdown

按 high confidence → medium → low 顺序改。每改一处：
- 引用准确 (file:line 数字最新)
- 中文措辞跟周围段落一致
- 链接活 (markdown link 没坏)

### 步骤 5: 自检

```bash
# 检查 markdown 死链
grep -rn "file:line" docs/ | head
# 检查 file:line 引用还存在 (随机抽 3 个)
```

### 步骤 6: 一并 commit

commit message 提到 doc sync：

```
feat(<scope>): <主改动>

代码改动: ...

文档同步:
  - docs/metrics-catalog.md   更新 Net Sales 公式 SQL 行号
  - docs/data-contract.md     补 ttpos_statistics_product.member_uuid 字段使用
```

## 不要做的事

❌ 改 `.cache/` / `exports/` (产物, 不进 git)  
❌ 改测试文件的引用 (tests 是代码, 不属于 doc sync)  
❌ 凭印象改文档 (改完一定要回去看代码确认)  
❌ 改"业务判断"的话术 (e.g. "Stars 应该主推" 这种业务建议, 用户改) 

## 失败兜底

- **AI 不确定是否要改** → 列在 "low confidence", 让用户决定
- **改完跑 grep 发现 file:line 错** → 重读代码再改
- **文档间互相引用 broken** → 报告给用户, 手动修

## 价值锚定

这个 skill 替代什么:
- 业界 dbt-osmosis / Cube.dev schema check (自动 lint)
- 大公司"文档跟代码漂移"告警

为什么 AI 替代工具:
- markdown 是 LLM 友好格式
- LLM 能 read code + read markdown + 判断同步, 比 schema lint 灵活
- 无维护成本 (skill 是文字, 不是代码 / 不会因 lib 升级挂)
- 适合小项目 (我们的规模, 引入工具反而是负担)

## 相关 skill

- [onboard-fact-table](../onboard-fact-table/SKILL.md) — 接新事实表流程
- [bigquery-export](../bigquery-export/SKILL.md) — BQ 表结构速查
