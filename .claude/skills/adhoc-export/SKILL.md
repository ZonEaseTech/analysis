---
name: adhoc-export
description: 市场/老板提临时导表需求, 或者市场甩新事实表过来要重导, 必走流程.
             覆盖 3 类情况: ttpos 直接有 / 要算 / 客户提供新事实表混进来.
             目的: 让 AI 在动手前完成"先审现状"硬动作, 不再凭直觉乱开干.
triggers:
  - 市场要个表
  - 临时导个数据
  - 老板想看
  - 帮我导一份
  - 重新导表
  - 市场补充 BOM
  - 客户给了新事实表
  - 接外部 Excel
  - 利润报表
  - profit_by_price
  - profit_margin
---

# Adhoc Export Skill

## 为什么有这个 skill

历次踩坑教训(每条都真实发生过):

| 坑 | 表现 | 这次活实例 |
|---|---|---|
| 用错 config | 跑出的数据全是过期事实表的 | 用 wallace.20260422 当默认, 实际生产是 .20260513 |
| 用错报表 | 客户要 profit_by_price 我给 profit_margin | 跑了 3 版 profit_margin 才发现 |
| 没看 ground truth | 凭印象动手, 没核对上一次报表的来源审计列 | v14 BOM来源列写的事实表跟我 config.yaml 不一致 |
| 信任第一次 audit | 数据是错的, 我却基于错数据出"修复方案" | "鸡块" 51 店误匹其实是错链路引发, 不是真问题 |

**根因**: AI 接到任务习惯"读需求 → 立即写代码", 跳过了"先看清楚现状"。
本 skill 把"看清楚现状"做成强制 0-3 步, 不允许跳过.

---

## 流程(必走, 不能跳)

### 步骤 0 — 审现状(动手前必做)

⚠️ **任何代码 / 配置 / 数据动作之前**, 完成以下 4 件事:

```bash
# 0.1  确认当前生产 config — 唯一一份, 就是 resources/config.yaml
cat resources/config.yaml
# 唯一活配置. 客户给了新数据 → priority 挂进这份; 不要往 resources/wallace.*/ 放 config.

# 0.2  确认上一次报表用了什么事实表 (ground truth)
ls -t exports/profit_by_price_*.xlsx | head -1
# 打开它, 读最后两列 (BOM来源 / 价来源), 记录所有出现的事实表名.

# 0.3  对照
# 上一次报表 BOM来源 列 vs resources/config.yaml 的 bom_sources 列表
# 不一致 → 先修 config, 再开始任务. 不要跳.

# 0.4  确认任务要的是哪份报表
# - 客户交付物:  bq_reports/profit_by_price_report.py   (按价展开 18 列)
# - 中间表/对账锚:  bq_reports/profit_margin_report.py  (BOM 物料展开 32 列)
# 看任务原话, 不要凭"补充 BOM"就猜成 profit_margin.
```

任一条 fail → **回到用户问清楚, 不要继续**.

### 步骤 1 — 拆需求

把市场原话拆成 4 维度, 对齐:

```
时间范围 = 2026-03 (单月? 多月? 滚动 7 天?)
粒度    = 店 × 商品 × 渠道  (or 店x月? 品类x周?)
指标    = 营业额 / 退款率 / 客单价  (具体哪几个)
筛选    = 仅外卖? 仅会员? 排除某店?
```

模糊 → 回问, 不猜. 列 3-5 个"我理解你要的是 X" 选项让用户选.

### 步骤 2 — 数据可用性判断

| 路径 | 怎么判 | 命中后去哪 |
|---|---|---|
| ✅ 销售事实 (entities 已有字段) | 翻 `docs/data-menu.md` | 直接拼 SQL |
| ⚠️ 销售衍生 (BOM/COGS/毛利率) | 翻 `docs/metrics-catalog.md` | 用 cogs/ + kpi_ratios | console 加"BOM 覆盖率 X%" |
| 🔍 ttpos 后端有, 我们未 mirror | 翻 `docs/ttpos-algorithms-mirror.md` 找 Go 函数 | **走 mirror 子流程**(下方) |
| ❌ 客户提供新事实表 | 走 `onboard-fact-table` skill | 接进 config, 再回到本 skill 步骤 0.3 重新对照 |
| ❌ ttpos / 客户都没 | 标"需外部数据" | 退回用户, 不要硬编 |

### 步骤 3 — ttpos mirror 子流程(仅 🔍 路径触发)

1. 读 `ttpos-server-go/main/app/repository/statistics.go` 对应函数(数百行 Go)
2. 翻成 Python SQL, 加到 `semantic/entities/<合适文件>.py`
3. 加最小单测 + `TtposAnchorCheck` 锚一次真实数据
4. 文档同步 (`ttpos-algorithms-mirror.md` 状态 ❌ 改 ✅)

### 步骤 4 — 拼报表脚本

cp `bq_reports/<已有最接近的>_report.py` → 新文件, 改 4 处:
- SQL 模板(组合 entities 的 CTE)
- yaml 列定义(`resources/reports/<新名>.yaml`)
- 校验器(默认套 `FULL_IDENTITIES`)
- 输出路径

### 步骤 5 — 跑 + 校验

```bash
venv/bin/python -m bq_reports.<脚本> --month 2026-03 --output exports/...
# --config 不传 → 默认读 resources/config.yaml
# 第一行日志 [Config] 加载: resources/config.yaml 是审计锚, 截图存档
```

校验器报 🔴 → **不交付**, 先做步骤 6 audit.

### 步骤 6 — 交付前 audit(必做, 不能跳)

```python
# 1. BOM来源 / 价来源分布:  跟上一次报表对比, 偏差 > 5% 要解释
# 2. 物料单价 = 0 的 unique code 列表 (sort by 累计行数)
# 3. 抽 3-5 个具体商品打开看, 看 COGS 数字是否合理
# 4. 跟 v14 (ground truth) 用同一 (store, item) 比, 数值偏差 > 2% 要解释
```

异常找到 → 别急着出 fix 方案, **先验证输入数据是不是对的**(踩过 4 次的坑).

---

## 特例:市场反复追加同一份表(重导循环)

最高频的真实场景 —— 市场给 v1 → 你导 → 他 review → 追加 v2 → 又导 → loop:

```
v1 → 清洗 csv → commit → 导报表 → 给市场 review
   ↓ 市场追加 v2 (通常是 v1 的超集/修订, 不是全新的表)
v2 → 重导 → review → v3 → ... loop
```

**判定走这条**:市场给的是"上次那张表的新版本"(同结构、超集或修订),
**不是**全新业务的表。全新表 → 走完整步骤 0-6。

每轮 loop 固定 5 步,不用重走 0-6:

```bash
# 1. 覆盖归档的原始文件 (文件名不变, 还是 _原始.xlsx)
cp /workspace/data/uploads/<新UUID>.xlsx \
   "resources/wallace.<日期>/<原名>_原始.xlsx"

# 2. 重跑清洗脚本 (RAW 已指向归档位, 直接重跑)
venv/bin/python scripts/adhoc/clean_<topic>.py

# 3. 看 diff —— 市场这版改了啥, 一目了然 (CSV 纯文本可逐行 diff)
git diff "resources/wallace.<日期>/<原名>.csv"
#   → 拿这段 diff 直接跟市场对齐: "你这版加了 X / 改了 Y, 对吧?"
#   → 沟通成本从"翻两个 Excel"降到"看一段 diff"

# 4. ⚠️ 清缓存 —— 最容易漏! 文件名没变, 缓存 key 基于 path,
#    不清会命中旧缓存, 白跑.
rm -f .cache/bq_reports/fallback_boms_v2_*.json

# 5. 重跑报表 + 走步骤 6 audit
venv/bin/python -m bq_reports.<脚本> --month <月> --output exports/...
```

**5 步里最容易漏的是第 4 步清缓存** —— 漏了表面跑成功, 数据还是旧的.

config.yaml 通常**不用改**(文件名/path 没变, adapter 没变). 只有市场换了
文件结构 / 加了新 sheet 才动 config.

step 5 跑完, **仍然走步骤 6 的交付前 audit** —— loop 不豁免 audit.

---

## 决定要不要进入 git

| 类型 | 处理 |
|---|---|
| 一次性需求(2026 Q1 临时) | 文件名带日期 `<topic>_<date>.xlsx`, 90 天后清理 |
| 反复用(每月固定) | 提到 `bq_reports/` 正式区 + 加 README + 加 sheet config yaml |
| 跑了但发现是误解需求 | xlsx 删, 脚本删, 不进 git |

---

## 失败兜底

| 卡点 | 兜底动作 |
|---|---|
| 步骤 0.3 对照不一致, 但客户没说要改 | 报告"当前 config 跟生产报表对不上, 是否先同步 config?", 等回复 |
| 步骤 1 拆需求拆不出来 | 列 3-5 个选项让用户选 |
| 步骤 2 ttpos / 客户都没 | 写一行"需要客户/财务提供 XX 事实表", 停 |
| 步骤 5 校验器红灯 | 不交付, 贴差异详情让用户 review |
| 步骤 6 数值偏差大 | **优先验输入数据**, 不要假设是 bug 直接改代码 |
| 用户中途改需求 | 别覆盖原文件, 新建一个, 避免破坏前面正在 review 的 |

---

## 这几件事永远不要做

- ❌ 跳过步骤 0, 拿到任务就开始 cp 脚本
- ❌ 在 metrics / 抽象层 上叠新东西(YAGNI; 平台撞车前科)
- ❌ 凭印象写 SQL(没翻 entities / data-menu 就动手)
- ❌ 跳过 validators 直接出表
- ❌ 把"客户外挂 BOM"这种语义改动当成"adhoc 改动"(走 `onboard-fact-table` skill)
- ❌ 第一次 audit 出问题就出"修复方案"(先验输入)
- ❌ 直接改报表代码而不是改 config

---

## 跟其他 skill 协作

```
adhoc-export                        ← 入口
   ├── (ttpos 已有未 mirror)
   │      └── 读 ttpos-server-go Go 源码 + mirror SQL
   ├── (客户给新事实表) → onboard-fact-table skill
   ├── (改完代码) → sync-docs skill  (commit 前文档同步)
   └── (查 BQ 表结构) → bigquery-export skill
```

---

## 维护规则

- 历次踩坑 → 加到顶部"为什么有这个 skill"表里
- 步骤太长说明流程僵了 → 检查能不能合并步骤(但**不能砍 步骤 0 / 6**)
- 工具变化(e.g. 加了新 skill / 新工具) → 更新"跟其他 skill 协作"

## 价值锚定

业界对标: dbt 的 `dbt audit` / Cube.dev 的 `cubejs-cli check` — 都是"动手前先审"的流程化工具.
我们用 skill markdown 替代, 因为 AI 是消费者, markdown 是 LLM 友好的强约束.
