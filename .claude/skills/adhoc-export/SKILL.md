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
| 漏 audit 项 | 每次现写检查脚本, 漏了 BOM 抽查 | 用户提醒才补 → 步骤 6 改用固定脚本 audit_report.py |
| 覆盖文件 | 手动 mv 加版本号, 覆盖了已存在的 v1 | mv/cp 不像 Write 会拦 → 步骤 5/7 走 auto-version 不手动编名 |
| 交付物无指纹 | 改了文件用户看不出 / 不知道是 AI 没改还是自己下错版本 | 客服待隐藏订单_2026-03.xlsx 反复迭代用户区分不出 → 步骤 7 强制内部版本号 + MD5 |
| adhoc 脚本散落 | scripts/adhoc/ 下 13+ 个未跟踪脚本, 3 个月后自己都不知道哪个查啥 | 步骤 8 强制 3 行头注释 + 按月归档 + 三次法则触发沉淀 |

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

**拆完后强制问自己 1 句(30 秒成本, ROI 最高的一步)**:

> "这个问题如果半年后再来一次, 我希望已经准备好什么?"

- 答"什么都不用, 临时跑就行" → 真 adhoc, 继续走流程
- 答"希望有个字段 / identity / 维度已经在 semantic 里" → **顺手多花 30 分钟做了再交付**
  这 30 分钟是"边接边沉淀"的关键, 比季度集中整理高效 10 倍.

### 步骤 2 — 数据可用性判断

**2.0 先扫 semantic 现成能力(不重写已有的)**:

```bash
# 找需要的字段在不在现有 entity 里
grep -rn "<关键字段或指标名>" semantic/entities/
# 命中 → 拼 SQL 时 import 现有 CTE (sale_event / sale_line / takeout_line / total_line)
# 没命中 → 才考虑从 BQ 原表写; 注意 60-70% 的销售类需求 sale_event 已覆盖
```

跳过 2.0 → 容易重新写一份 sale_event 已经能拼出来的 SQL(过去踩过).


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
# ⚠️ 对外交付报表: 不传 --output, 走 auto-version (脚本自动 _v{N+1}).
#    手动传 --output 自己编文件名 → 容易 mv/cp 覆盖已存在版本 (踩过).
venv/bin/python -m bq_reports.<脚本> --month 2026-03
# --config 不传 → 默认读 resources/config.yaml
# 第一行日志 [Config] 加载: resources/config.yaml 是审计锚, 截图存档
```

校验器报 🔴 → **不交付**, 先做步骤 6 audit.

### 步骤 6 — 交付前 audit(必做, 不能跳)

**跑 audit 脚本, 不要现写**(现写会漏项, 上次就漏了 BOM 抽查):

```bash
venv/bin/python scripts/audit_report.py exports/<刚跑的报表>.xlsx [更多...]
```

一条命令出齐 4 项 + 交付说明草稿:
1. BOM来源 / 价来源 分布 — 确认事实表来源符合预期
2. 物料单价 <= 0 的 unique code — strict 模式缺价物料
3. **BOM 抽查** — 每 sheet 抽 3 个商品, 打印物料明细 + COGS + 毛利率
4. "无 BOM" 商品数 — 新事实表没覆盖的

异常找到 → 别急着出 fix 方案, **先验证输入数据是不是对的**(踩过 4 次的坑).

### 步骤 7 — 交付

1. **文件名**: 走 auto-version (步骤 5 不传 --output 就自动了). 不要手动 mv 加版本号.
2. **每个 xlsx 输出必须带指纹** — **不能省, 不管是正式报表还是 adhoc 临时表**:
   - **文件名版本号**: 走 `_v{N}.xlsx` 命名 (跟 bq_reports/profit_by_price_report.py 的 `_next_version_path()` 一致).
     扫 exports/ 已有版本 → max+1 → 新文件不覆盖旧版. **这是最显眼的版本标记, 优先用这个**.
   - **内部版本号 (兜底)**: 同时把版本号写「说明」sheet A1 (红色加粗) + workbook title.
     用户万一只盯文件内容不看文件名也能看出来.
   - **console 打印**: 跑完最后一行 print MD5 + 大小 + 修改时间, 例如:
     ```
     输出: exports/<文件>.xlsx
       内部版本: v3
       修改时间: 2026-05-15 03:17:04
       大小:     16691 bytes
       MD5:      2dc59c52ea71b1829305b34403c63a3b
     ```
   - **回复用户时把 MD5 贴出来**. 用户能拿 MD5 自验下载到的就是你刚导的那份, 不是浏览器缓存 / SCP 残留.
   - **参考实现**: `bq_reports/profit_by_price_report.py` 的 `_next_version_path()` (文件名版本号).
   - **为什么强制**: 同文件名反复迭代时用户区分不出新旧 → 怀疑 AI 没改 / 自己下错 → 信任崩盘. 加这两个字段成本几乎为 0.
3. **交付说明**: audit 脚本末尾的「交付说明草稿」自己核一遍, 补两类必说项:
   - 换过事实表 → 毛利率变化是不是口径修正 (是修正不是变差, 要讲清楚)
   - "无 BOM" / 缺价物料多的 → 那些商品成本虚高/偏低, 让市场别当真
4. **交付物**: 报表 xlsx + 差异报告 (如有) + 交付说明 + MD5. 一起给.

### 步骤 8 — 交付后归档(adhoc 脚本不能散落)

⚠️ **任何写在 scripts/adhoc/ 下的脚本, 交付完成后必须做完这 3 件事才能算结束**:

```bash
# 1. 脚本头部加 3 行注释(下次自己 / 别人能看懂这脚本在查啥)
#    强制格式:
#       # 谁问的: <角色 / 客户>  /  <日期 YYYY-MM-DD>
#       # 问什么: <一句话, 不超过 80 字>
#       # 结论:   <数据结论 + 后续行动: 已沉淀 / 一次性 / 待复盘>

# 2. 按月归档(不要散落在 scripts/adhoc/ 根目录)
mkdir -p scripts/adhoc/_archive/$(date +%Y-%m)
mv scripts/adhoc/<新脚本>.py scripts/adhoc/_archive/$(date +%Y-%m)/

# 3. 三次法则统计(顺手扫同类主题出现频次)
ls scripts/adhoc/_archive/*/*<关键词>* 2>/dev/null | wc -l
# >=3 → 输出"沉淀建议清单"给用户:
#       "<keyword> 类问题已出现 3 次, 建议提到 semantic/entities/ 或 validators/"
#       不直接动 semantic, 让用户决策.
```

**为什么强制 8.1**: 3 行注释成本 30 秒, 不写, 3 个月后整个 _archive/ 是死代码.
**为什么强制 8.2**: 散落在根目录 → 下次 grep 找现成参考全是噪声.
**为什么强制 8.3**: "三次法则" 是边接边沉淀的触发器, 不靠人脑记忆.

跳过步骤 8 → 仓库腐烂的根因. 没商量.

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
- ❌ 跳过步骤 2.0 直接写 SQL(60-70% 销售类需求 sale_event 已经能拼)
- ❌ 跳过步骤 8 归档, adhoc 脚本散落在 scripts/adhoc/ 根目录(仓库腐烂根因)
- ❌ 看到三次法则触发了, 立刻动 semantic(只输出"建议清单"给用户决策, 不自己拍板)

---

## 跟其他 skill 协作

```
adhoc-export                        ← 入口
   ├── (ttpos 已有未 mirror)
   │      └── 读 ttpos-server-go Go 源码 + mirror SQL
   ├── (客户给新事实表) → onboard-fact-table skill
   ├── (改完代码) → sync-docs skill  (commit 前文档同步)
   └── (查 BQ 表结构 / SQL 语法) → docs/bq-schema-reference.md + docs/bq-sql-patterns.md
```

> `bigquery-export` skill 已合并归档. Schema 速查见 `docs/bq-schema-reference.md`, SQL 语法速查见 `docs/bq-sql-patterns.md`.

---

## 维护规则

- 历次踩坑 → 加到顶部"为什么有这个 skill"表里
- 步骤太长说明流程僵了 → 检查能不能合并步骤(但**不能砍 步骤 0 / 6**)
- 工具变化(e.g. 加了新 skill / 新工具) → 更新"跟其他 skill 协作"

## 价值锚定

业界对标: dbt 的 `dbt audit` / Cube.dev 的 `cubejs-cli check` — 都是"动手前先审"的流程化工具.
我们用 skill markdown 替代, 因为 AI 是消费者, markdown 是 LLM 友好的强约束.
