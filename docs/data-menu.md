# 数据菜单 — 我们能给你什么数据

> **给市场 / 客户 / 老板 / 销售看**。不需要懂代码 / SQL / 缓存。
>
> 用法：客户问"想看 X" → 你打开本菜单 → 看"能给"还是"不能给" →
> 直接报答案 + 给出报表来源。
>
> 设计理念：业界叫 **Data Product Catalog** / **Self-Serve Menu** —
> 把所有"业务可问问题"列成菜单，**业务方翻菜单点单**，而不是无头绪地问。
>
> 归档日期 2026-05-13。

## 0. 你能拿到的数据是哪个范围

✅ **能给的**:
- 餐厅销售数据 (53+ 家泰国门店, 月度 / 日度)
- 商品 / 套餐销量 / 价格
- BOM 物料成本 (含三方成本表对账)
- 堂食 vs 外卖分渠道
- 跟 ttpos 后台一致 (差额 < 0.001%)

❌ **不能给的** (诚实声明边界):
- 平台真实抽佣 (Grab/LINE MAN 实际扣了多少) — 需要平台月度对账单
- 商家银行真实到账 — 需要银行流水
- 人力成本 / 房租 / 水电 — 需要财务/HR 数据
- 顾客复购 / 会员消费习惯 — ttpos 不采顾客信息
- 实时数据 — 当前 BQ 同步有 24-48 小时延迟

## 1. 维度菜单 (按什么分组看)

✅ = 立即能给; ⚠️ = 能给但要加一行 SQL; ❌ = 没数据

| 维度 | 状态 | 例子 |
|---|---|---|
| **按店** | ✅ | "53 家店各赚多少" |
| **按月** | ✅ | "2026 年 1-12 月趋势" |
| **按日** | ⚠️ | "2026-04 每天营业额" (需补一份日级聚合) |
| **按 SKU / 商品** | ✅ | "炸鸡桶销量排行" |
| **按价格档** | ✅ | "同一 SKU 不同价位卖量" |
| **按渠道 (堂食/外卖)** | ✅ | "外卖占多少" |
| **按平台 (Grab/LINE MAN/Shopee)** | ⚠️ | takeout_order.platform 字段在但报表没用上 |
| **按商品分类 (主食/饮料/小吃)** | ⚠️ | 要加 category JOIN |
| **按 BOM 物料** | ✅ | "番茄酱用了多少" |
| **按时段 (早午晚)** | ❌ | 当前报表都按月聚合, 需要新建 daypart entity |
| **按收银员** | ❌ | ttpos 字段有但没接 |
| **按支付方式** | ⚠️ | ttpos 字段有, 报表没用 |
| **按主体 (法人公司)** | ❌ | 需要客户提供主体映射 |

## 2. 指标菜单 (看什么数字)

### 销售指标

| 指标 | 状态 | 含义 / 在哪看 |
|---|---|---|
| **总营业额 (GMV)** | ✅ | 全部交易标价金额 — pnl Sheet 1 |
| **净销售额 (Net Sales)** | ✅ | 扣完损失项 — pnl Sheet 1, 跟 ttpos 后台一致 |
| **销量** | ✅ | 件数 — profit_margin Excel |
| **客单价 (AOV)** | ✅ | 客户每单平均花多少 — pnl KPI Dashboard |
| **退款金额** | ✅ | pnl Sheet 1 |
| **赠品/赠送金额** | ✅ | 同上 |
| **调价折扣金额** | ✅ | (堂食) 实际成交价比标价低多少 |
| **外卖取消金额** | ✅ | state=60 订单价值 |

### 成本指标

| 指标 | 状态 | 含义 / 在哪看 |
|---|---|---|
| **物料成本 (COGS)** | ✅ | BOM × 销量 × 单价 — profit_margin 单份总成本 / pnl Sheet 1 |
| **人力成本** | ❌ | 待 HR 系统接入 |
| **房租** | ❌ | 待财务接入 |
| **水电** | ❌ | 待财务接入 |
| **营销费用** | ❌ | 待营销 / 财务接入 |
| **平台抽佣** | ⚠️ 估算 | 按 28% 默认率估算 — pnl Sheet 1; 真值待对账单接入 |
| **配送费** | ❌ | ttpos 字段有但不在结算视野 |
| **支付通道费** | ❌ | 银行/支付通道账单 |

### 利润指标 (按业界财务标准分层)

| 指标 | 状态 | 含义 |
|---|---|---|
| **销售毛利 (Gross Profit)** | ✅ | Net Sales − COGS — 餐饮老板最关心 |
| **毛利率 (Gross Margin %)** | ✅ | GP / Net Sales — 跟行业基准 60-70% 对照 |
| **贡献毛利 (Contribution Margin)** | ⚠️ 估算 | GP − 变动成本(估算抽佣) |
| **经营利润 (Operating Income / EBIT)** | ❌ | 待固定成本接入 |
| **净利润 (Net Income)** | ❌ | 财务系统出, 不在 BQ 范围 |

### 行业 KPI

| 指标 | 状态 | 行业基准 |
|---|---|---|
| **Gross Margin %** | ✅ | 餐饮 60-70% |
| **Food Cost %** | ✅ | 28-35% |
| **Labor Cost %** | ❌ | 25-30% (待人力) |
| **Prime Cost %** | ❌ | 55-65% 餐饮核心指标 (待人力) |
| **Operating Margin %** | ❌ | 8-15% (待固定成本) |
| **Effective Take Rate** | ⚠️ 估算 | 20-30% |

### 渠道分析

| 指标 | 状态 |
|---|---|
| **堂食占比 / 外卖占比** | ✅ |
| **堂食 GM% vs 外卖 GM%** | ✅ — 直接回答"外卖赚不赚钱" |
| **堂食 vs 外卖 客单价** | ⚠️ |
| **各平台占比** (Grab/LINE MAN/Shopee) | ⚠️ |

### 菜单工程 (餐饮专有)

| 指标 | 状态 | 含义 |
|---|---|---|
| **Stars ⭐** | ✅ | 高销量高毛利 (主推) |
| **Plowhorses 🐴** | ✅ | 高销量低毛利 (提价/换料) |
| **Puzzles 🧩** | ✅ | 低销量高毛利 (推广) |
| **Dogs 🐕** | ✅ | 低销量低毛利 (下架) |

→ pnl Sheet 5 菜单工程矩阵, 53 店 × 9730 SKU 已自动分类

### 跨期对比

| 指标 | 状态 |
|---|---|
| **MoM (环比上月)** | ✅ pnl Sheet 1 自动跑 |
| **YoY (同比去年)** | ⚠️ 数据满 12 个月后自动启用 |
| **量差/价差/成本差/结构差 4 维归因** | ✅ pnl Sheet 7 — 老板问"为什么变了" 自动答 |

## 3. 现成报表清单 (已有的"菜")

直接跑 CLI 拿 Excel:

### 报表 1: 利润中间表 `profit_margin`
```bash
venv/bin/python -m bq_reports.profit_margin_report \
    --month YYYY-MM --summary --allow-erp-fallback \
    --output exports/...xlsx
```
**给谁看**: 店长 / 采购  
**粒度**: 店 × SKU × BOM 物料  
**看什么**: 每个 SKU 每条 BOM 物料的成本、数量、单价、来源

### 报表 2: 按价格档利润 `profit_by_price`
```bash
venv/bin/python -m bq_reports.profit_by_price_report --month YYYY-MM ...
```
**给谁看**: 营销  
**粒度**: 店 × SKU × 价格档 (N=5 + 其它)  
**看什么**: 同一 SKU 在不同价位卖了多少, 价格策略效果

### 报表 3: 财务 P&L `pnl_statement` ⭐ 推荐
```bash
venv/bin/python -m bq_reports.pnl_statement \
    --month YYYY-MM --allow-erp-fallback --compare-with YYYY-MM
```
**给谁看**: 老板 / 财务 / 经营层  
**粒度**: 集团 / 按店 / 按渠道 / 按 SKU 全维度  
**7 sheet 完整 drill-down**:
- Sheet 1 集团损益表 — 总览 GMV → Net Sales → Gross Profit → Contribution
- Sheet 2 KPI Dashboard — 健康度评级
- Sheet 3 按店损益 — 53 店排名
- Sheet 4 按渠道对比 — 堂食 vs 外卖
- Sheet 5 菜单工程矩阵 — Stars/Plowhorses/Puzzles/Dogs
- Sheet 6 数据来源审计 — 每个数字怎么来的
- Sheet 7 跨期差异分解 — 量/价/成本/结构 4 维归因

## 4. 现成对账 (跟外部系统比对的硬证据)

| 对账锚 | 状态 | 差额 |
|---|---|---|
| **跟 ttpos 后台 CountSale 一致** | ✅ 自动 | 69 元 / 3464 万 ≈ 0.0002% |
| **跟 Grab 月度对账单** | ❌ 待客户提供 |
| **跟 LINE MAN 月度对账单** | ❌ 待客户提供 |
| **跟 Shopee Food 月度对账单** | ❌ 待客户提供 |
| **跟客户法定财报** | ❌ 待客户提供 |
| **跟 ERPNext 物料出库** | ⚠️ 框架就绪, 未跑 |

每个对账 OK 时给客户的话术：

> "BQ 算的 Net Sales 跟 ttpos 后台显示的数字差 < 0.001%，跑 391 个测试零失败。可以放心拿这个数字。"

## 5. 典型客户问题 → 看哪份报表

| 客户问什么 | 你打开什么 |
|---|---|
| "4 月集团利润多少" | pnl Sheet 1 总览 |
| "外卖到底赚不赚钱" | pnl Sheet 4 按渠道对比 (回答 CM% 真实值) |
| "哪家店最差" | pnl Sheet 3 按店损益, 按 GP 升序 |
| "为什么这月毛利跌了" | pnl Sheet 7 差异分解 (量/价/成本/结构) |
| "哪些 SKU 要下架" | pnl Sheet 5 菜单工程 Dogs 象限 |
| "这个数字怎么算的" | pnl Sheet 6 审计 或 docs/metrics-catalog.md |
| "我们跟 ttpos 一致吗" | console TtposAnchorCheck 输出 |
| "ABC 店外卖客单价" | profit_by_price 报表 + 自己 SUM |
| "番茄酱用了多少 / 成本多少" | profit_margin 单品 sheet 找该物料 |
| "套餐 vs 单品哪个赚" | profit_margin 套餐 sheet + 单品 sheet 对比 |

## 6. 引导客户的话术模板

当客户提模糊需求时（"我想看销售数据"），用这套问句引导：

```
1. "要看哪个时间范围？" (月 / 季度 / 自定义)
2. "要看全集团还是某几家店？"
3. "要看哪些维度?" → 翻本菜单第 1 章, 圈出 ✅
4. "要看哪些指标?" → 翻本菜单第 2 章, 圈出 ✅
5. "要不要跨期对比?" (单期 / MoM / YoY)
```

5 个问题问完，**90% 的需求可以直接对应一份现成报表 + 一两个 sheet**。

剩下 10% 走"新报表需求"流程（场景 7 in `docs/work-scenarios-runbook.md`，未来归档）。

## 7. 已知数据 / 数字异常 (主动告知客户)

要主动跟客户说清的"已知问题"，避免客户拿到数据后追问：

| 数字 | 状态 | 解释 |
|---|---|---|
| 单品 Gross Margin -21.9% | 🔴 异常但是真的 | ERP 物料单价虚高 + fallback_bom 误匹配, 业务侧治理中 |
| "香脆全鸡" 毛利率 -455% | 🔴 数据治理 | fallback_bom 误匹配半只配方; 已被 SOURCE_COVERAGE 揪出, 待修配置 |
| 外卖 CM% -51% | 真的 | 估算 28% 抽佣后, 反映外卖业务结构性亏损 |
| Labor Cost / Prime Cost / Operating Income 全 N/A | 真的 | 待 HR + 财务 ERP 数据接入 |
| 平台抽佣是估算 | 真的 | 业界做法; 真账待平台对账单接入 |

## 8. 我们的承诺 / 不承诺

✅ **承诺**:
- 销售数字跟 ttpos 后台对账 < 0.001% 差额
- 任何数字都能追溯到 SQL / 字段级 (Sheet 6 审计)
- 跨月对比能自动归因 (Sheet 7)
- 接新事实表 1-2 小时上线 (走 `/onboard-fact-table` skill)
- 改算法 / 改口径 byte-equal 验证 (391 测试 + 30 parity tests)

❌ **不承诺**:
- 商家真实到手利润 (没接对账单)
- 经营利润 (没接固定成本)
- 实时数据 (BQ 24-48 小时延迟)
- 跟法定财报对账 (需要客户财务先 sign-off 准则差异)

## 9. 数据质量自检 (跑报表时 console 输出)

每次跑利润报表，console 自动输出：

```
[校验] 销量恒等式       ✅ N 通过
[校验] 金额恒等式       ✅ N 通过
[校验] BOM 来源完整性   🔴 X 离谱 (有销量但没 BOM)
[校验] 物料单价来源     ✅ N 通过
[校验] 退款率合理性     🟡 X 复核 / 🔴 X 离谱
[校验] 赠品赠送率       ✅
[校验] 外卖取消率       🟡 X 复核
[校验] TtposAnchor      ✅ delta 0.0002%
```

🔴 出现 = 给客户报表前要先 sign-off。

## 10. 持续扩展 (我们的下一步)

正在做 / 未来想做（市场可以提需求）:

- ✅ 已完成: 7-sheet P&L + 5 类对账 + 4 维归因
- 🔄 进行中: skill 化 (AI 自动接新表)
- ⏳ 待客户配合: 平台对账单 / 法定财报 / HR / 财务 ERP 接入
- ⏳ 未来: 飞书大屏 / 对话入口 (NL2SQL)

## 相关文档 (工程视角, 客户不用看)

- [docs/metrics-catalog.md](./metrics-catalog.md) — 工程师视角的口径地图 (含 SQL/文件:行号)
- [docs/ttpos-bq-field-pitfalls.md](./ttpos-bq-field-pitfalls.md) — ttpos 字段陷阱清单
- [docs/architecture-evolution-roadmap.md](./architecture-evolution-roadmap.md) — 平台架构演进
- [.claude/skills/onboard-fact-table/SKILL.md](../.claude/skills/onboard-fact-table/SKILL.md) — 接新表 skill
