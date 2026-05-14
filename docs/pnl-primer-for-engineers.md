# 工程师视角的 P&L 损益表入门

> 给非财务/工商专业出身的工程师看。让你能：
> 1. 看懂财务/老板说的"行话"
> 2. 跟财务部门沟通时不外行
> 3. 判断报表数字对不对（识别异常）
> 4. 知道哪些"行业标准做法"我们必须有
>
> 归档日期 2026-05-13。

## 1. 业界 P&L 标准长什么样

任何上市公司的财报、任何 ERP 系统的 "Income Statement" / "损益表"，**核心结构都是这一套**：

```
┌─────────────────────────────────────────────────┐
│ Revenue / Sales                  营业收入         │  ← 总收入（毛收入）
│   Less: Returns & Allowances     退货/折让        │
│   Less: Discounts                 折扣            │
│ ─────────────────                                  │
│ = Net Revenue / Net Sales        净营业收入       │
├─────────────────────────────────────────────────┤
│   Less: COGS                     销货成本         │  ← 直接成本
│ ─────────────────                                  │
│ = Gross Profit                   毛利             │ ← 关键指标 #1
│   Gross Margin %                 毛利率           │
├─────────────────────────────────────────────────┤
│   Less: Operating Expenses (OpEx)运营费用         │
│     - Selling Expenses           销售费用         │  marketing/广告/抽佣
│     - General & Admin (G&A)      管理费用         │  房租/人力/水电
│     - R&D                        研发费用         │  (餐饮可忽略)
│ ─────────────────                                  │
│ = Operating Income / EBIT        经营利润         │ ← 关键指标 #2
│   Operating Margin %             经营利润率       │
├─────────────────────────────────────────────────┤
│   ± Other Income/Expense         其它收入/支出     │  汇兑/资产处置
│   Less: Interest Expense         利息费用         │
│ ─────────────────                                  │
│ = EBT (Earnings Before Tax)      税前利润         │
│   Less: Income Tax               所得税           │
│ ─────────────────                                  │
│ = Net Income / Net Profit        净利润           │ ← 关键指标 #3
│   Net Margin %                   净利率           │
└─────────────────────────────────────────────────┘
```

**记住三个关键节点**：Gross Profit（毛利）/ Operating Income（经营利润）/ Net Income（净利润）。
财务和老板嘴里反复说的"利润"基本是这三个之一。

## 2. 关键术语字典（必懂）

| 缩写 | 全称 | 中文 | 含义 |
|---|---|---|---|
| **GMV** | Gross Merchandise Value | 商品交易总额 | 平台/电商常用，跟 Gross Revenue 同义 |
| **Revenue** | — | 营业收入 | 毛收入（不扣退款折扣） |
| **Net Sales** | — | 净销售额 | 扣完退款折扣 |
| **COGS** | Cost of Goods Sold | 销货成本 | **直接成本**：物料、包装、生产工人 |
| **Gross Profit** | — | 毛利 | Net Sales − COGS |
| **Gross Margin** | — | 毛利率 | Gross Profit / Net Sales × 100% |
| **OpEx** | Operating Expenses | 运营费用 | 跟生产销售没直接关系的费用 |
| **SG&A** | Selling, General & Admin | 销售管理费用 | OpEx 的两个大头合并 |
| **EBIT** | Earnings Before Interest & Tax | 息税前利润 | = Operating Income |
| **EBITDA** | EBIT + Depreciation + Amortization | 息税折旧摊销前利润 | 看现金创造能力的常用指标 |
| **CM** | Contribution Margin | 贡献毛利 | (Revenue − Variable Costs) / Revenue |
| **Take Rate** | — | 平台抽佣率 | 平台从商家收的比例 |
| **AOV** | Average Order Value | 客单价 | Revenue / Order Count |
| **MoM** | Month over Month | 环比 | 这月 vs 上月 |
| **YoY** | Year over Year | 同比 | 今年 vs 去年同期 |
| **Variance** | — | 差异 | 实际 vs 预算的偏差 |
| **Reconciliation** | — | 对账 | 跨系统数据核对 |

## 3. 直接成本 vs 变动成本 vs 固定成本（最容易搞混）

财务把所有成本按两个维度分：

### 维度 A：是否跟"卖出去的东西"直接相关

```
直接成本 (Direct Cost) = COGS    → 进毛利公式分子
间接成本 (Indirect Cost) = OpEx  → 在毛利下面扣
```

**餐饮行业**：
- 直接成本：**食材原料**（这就是我们 BOM 算出来的物料成本）、餐盒包装
- 间接成本：人力（厨房+前厅）、房租、水电、营销、平台抽佣

### 维度 B：是否随销量变动

```
变动成本 (Variable Cost): 销量 × 单位变动成本
  - 食材（卖一份扣一份的料）
  - 平台抽佣（按 GMV % 抽）
  - 配送费（按订单数）
  - 支付通道费

固定成本 (Fixed Cost): 不管卖多少都要付
  - 房租
  - 全职员工底薪
  - 水电基础费
  - 系统软件订阅
```

**为什么财务关心这个区分**？因为它决定**盈亏平衡点 (Break-even Point)**：

```
盈亏平衡销量 = 固定成本 / (单价 − 单位变动成本)
            = 固定成本 / 单位贡献毛利
```

老板问"这家店要卖多少才能不亏？"——靠这个公式答。所以财务报表里 **变动成本和固定成本必须分开列**，不能混在 OpEx 里一坨。

## 4. 关键比率（财务的"职业反射"）

财务看 P&L 第一眼就看这几个**比率**，不是绝对数字：

### 必看比率

| 比率 | 公式 | 健康值（餐饮行业） |
|---|---|---|
| **Gross Margin %** | Gross Profit / Net Sales | **60-70%**（食材成本控制好的话） |
| **Food Cost %** | 食材成本 / Net Sales | **28-35%**（行业经验） |
| **Labor Cost %** | 人力 / Net Sales | **25-30%** |
| **Prime Cost %** | (食材+人力) / Net Sales | **60-65%**（餐饮核心指标）⭐ |
| **Operating Margin %** | Operating Income / Net Sales | **8-15%** |
| **Net Margin %** | Net Income / Net Sales | **3-9%** |

> ⭐ **Prime Cost** 是餐饮行业最重要的运营指标——食材 + 人力两大开销之和。超过 65% 这家店就在挣扎，低于 55% 才算好店。比单看食材成本或单看人力成本更全面。

### 渠道指标（餐饮电商必看）

| 比率 | 公式 | 含义 |
|---|---|---|
| **Channel Mix** | 各渠道 GMV / 总 GMV | 渠道占比，看依赖度 |
| **Effective Take Rate** | 平台抽佣 / 平台 GMV | **真实抽佣率**（含各种费） |
| **Average Check** / **AOV** | Revenue / 订单数 | 客单价 |
| **Same-Store Sales (SSS)** | 老店 GMV YoY | 同店增长，看"是开新店带来的增长还是真增长" |

### 财务一定会问的几个对比

```
1. MoM (环比): 比上个月增长多少？
2. YoY (同比): 比去年这个月增长多少？
3. Budget vs Actual: 跟预算差多少？为什么？
4. Same-store: 老店是不是也在增长？
```

**任何一个绝对数字都不重要，重要的是它的"变化"和"占比"**。报表如果只给绝对数字、不给比率和对比，财务会觉得"信息不够"。

## 5. 财务人员看报表的思维模式

不是"看见数字记下来"，而是**反射式地几个动作**：

### 动作 1：看比率，不看绝对值

```
"营业额 3,623 万" → 没意义，太多变量
"毛利率 65%" → 立刻知道是好是坏（餐饮 60-70% 健康）
"Prime Cost 62%" → 立刻知道运营效率
```

### 动作 2：找异常 (Variance Analysis)

```
"这个月毛利率 58%（上月 65%）" → 立刻警觉，问：
  - 是 Net Sales 跌了？(分母变小)
  - 还是 COGS 涨了？(分子原料涨价了？)
  - 还是 Mix 变了？(便宜的低毛利产品卖多了？)
```

### 动作 3：拆责任 (Cost Allocation)

```
集团整体 -55,384 让利 → 谁的责任？
  - 营销活动？(番茄炸鸡桶促销, 你们让我做的)
  - 运营做的？(店长自主调价?)
  - 平台做的？(Grab 促销补贴, 你们要的)
```

财务最不喜欢"一坨成本/损失说不清谁负责"。报表必须能**按责任主体拆**。

### 动作 4：对账三方核对 (Three-way Match)

```
ttpos 后台数字 = BQ 报表数字 = 财务 ERP 数字 ?
  跟 ttpos 对不上 → 我们的 SQL 错了
  ttpos 跟 ERP 对不上 → ttpos 数据本身错
  ERP 错 → 财务录入错
```

每个数字都要能溯源到三个独立系统中的至少一个，才叫"可审计"。

## 6. 餐饮行业特有的分析框架

### Daypart Analysis（时段分析）

```
早餐 | 午餐 | 下午茶 | 晚餐 | 夜宵
```

每个时段的 GMV / Average Check / Margin 单独看。餐饮老板每天都看。

### Menu Engineering（菜单工程）

按"销量 × 毛利率"四象限分类：

```
                高销量
                  │
   "Plowhorses"   │   "Stars"
  (高销量低毛利)  │  (高销量高毛利)
                  │
   低毛利────────┼────────高毛利
                  │
     "Dogs"       │   "Puzzles"
   (低销量低毛利) │  (低销量高毛利)
                  │
                低销量
```

- **Stars** ⭐：旗舰产品，主推
- **Plowhorses** 🐴：走量但不赚钱，看能不能提价或换成本更低的料
- **Puzzles** 🧩：高毛利但卖不动，营销推一下能不能起来
- **Dogs** 🐕：双输，考虑下架

我们的 `profit_by_price` 报表加点逻辑就能做这个矩阵。

### 同店增长 (Same-Store Sales Growth)

餐饮老板最关心的"我们到底在不在长大"。

```
新店增长 = 开了新店带来的 GMV 增长
同店增长 = 老店本身的 GMV 增长

如果 总增长是正的但同店增长是负的 → 危险信号
（靠新店开张撑场子，老店其实在萎缩）
```

## 7. 财务专业人员是怎么"做" P&L 的（流程）

### 月度结账 (Month-end Close) 标准流程

```
T+1  ~  T+5 (5 天内):
  1. 收集所有原始数据（POS、平台、银行、ERP）
  2. 跑对账 (Reconciliation)
     - POS vs 平台对账单
     - 平台对账单 vs 银行流水
     - 现金日结 vs 银行存款
  3. 调整分录 (Adjusting Entries)
     - 应计费用 (Accrued Expenses)
     - 预付费用摊销 (Prepaid Expense Amortization)
     - 折旧 (Depreciation)
  4. 出 Trial Balance（试算平衡表）
  5. 出 P&L + Balance Sheet + Cash Flow Statement 三表
  6. Variance Analysis (vs 上月 / vs 预算)
  7. Management Reporting (给老板的执行摘要)
```

我们目前能做到的是 **POS → BQ 报表**这一段，相当于流程的第 1 步和第 2 步的一部分。
后面的"调整分录"、"三表合一"、"差异分析"是财务 ERP 的活，我们做不了——但应该**输出给财务 ERP 能直接消费的数据**。

### 输出标准（行业惯例）

财务系统消费我们数据时，最常见的格式：

1. **GL Entries**（总账分录）：按会计科目（如"主营业务收入"/"主营业务成本"/"销售费用-平台抽佣"）逐笔
2. **Department × Account × Period 立方体**：财务标准的多维 OLAP
3. **PDF 或 Excel 月度财报**：给非财务管理层的总结

我们的 P&L 报表归在 **第 3 类**——给老板/经营层看的可读版本，不是给财务做账的原始数据。

## 8. 报表展示的财务化惯例（这就是为什么我推荐"财务化风格"）

### 数字格式

- **千分位**：`36,232,832` 不是 `36232832`
- **负数括号**：`(55,384)` 不是 `-55,384`（财务标准，比负号更明显）
- **百分比 2 位小数**：`-0.15%` 不是 `-0.153%` 或 `0.001528`
- **单位列出**：`千 THB`、`百万 THB`

### 行结构

- **小计行加粗**：Net Sales / Gross Profit / Operating Income 这种关键节点要视觉突出
- **缩进表层级**：减项内缩 2-4 字符
- **分隔线**：每个关键节点上一根横线
- **N/A 显式**：缺失数据写"N/A (待接入)"，不要留空

### 比率列

每个数字旁边一定有：
- **% Net Sales**（占净销售比）
- **vs 上月 %**（环比）
- **vs 同期 %**（同比，跟去年这个月比）
- **vs 预算 %**（如果有预算）

### 颜色规则

- **负值红色**：损失项标红
- **百分比超阈值标黄/红**：例如 Prime Cost > 65% 标红
- **改善/恶化箭头**：↑↓ 加颜色，比看数字快

## 9. 我们现在缺什么（用这份知识 audit 之前的 design doc）

对照本文档要求 vs `docs/pnl-statement-design.md`，**遗漏的部分**：

| 行业标准要素 | design doc 是否有 | 缺失说明 |
|---|---|---|
| 5 层 P&L 结构 | ✅ | — |
| Gross Margin / Operating Margin 等比率 | ❌ | 没列必看比率清单 |
| Prime Cost（餐饮核心指标） | ❌ | 完全没有 |
| Food Cost % / Labor Cost % | ❌ | 没有行业标准比率 |
| MoM / YoY 对比 | ⚠️ 标"Phase 4 不做" | 应该 Phase 1 就做 MoM，太关键 |
| 同店增长 (SSS) | ❌ | 完全没有 |
| 渠道占比 (Channel Mix) | ⚠️ Sheet 3 部分有 | 没有正式比率列 |
| 客单价 (AOV) | ❌ | 完全没有 |
| 菜单工程矩阵 | ❌ | 没有 |
| 千分位/负数括号格式 | ❌ | 没明确 |
| 财务对账三方 | ⚠️ 只对 ttpos，未对 ERP | ERP 对接待沟通 |
| 调整分录支持 | ❌ | 完全没有 |

**遗漏大头**：

1. **比率列**：我们设计的是 "金额 + % GMV"，但财务标准要 **% Net Sales + vs 上月 + vs 同期**
2. **餐饮关键指标**：Prime Cost / Food Cost % / Labor Cost % / AOV 这些必须有
3. **MoM 对比**：单期数字没意义，必须出现"上月数字 + 变化 %"
4. **格式化惯例**：千分位、负数括号、关键节点加粗——这些都是给老板/财务看的"专业感"必备

## 10. 下一步：升级 design doc

带这份知识回去 audit 设计稿，补齐：

1. **比率列**完整化（不只 % GMV，加 % Net Sales / MoM / YoY）
2. **餐饮行业指标**专门一栏（Prime Cost / Food Cost % / Labor Cost % / AOV）
3. **MoM 对比**纳入 Phase 1（不是 Phase 4）
4. **菜单工程矩阵**作为可选 Sheet 5
5. **格式化规则**明确写到 yaml 配置

完了再开始 Phase 1 实施。

## 11. 你跟老板/财务沟通时的"安全词"

如果财务跟你聊 P&L，下面这几句话能让你显得"懂"：

- "Net Sales 是我们对齐的口径，跟 ttpos 后台 TotalReceivedAmount 一致"
- "我们目前能算到 Gross Profit 这一层；Variable Costs 第 3 层需要接平台对账单"
- "Prime Cost 这块要等接入人力数据后才能算"
- "MoM 对比下个月就能跑了，YoY 要等数据满 12 个月"
- "Channel Mix 显示外卖占 22%，毛利率比堂食低 X%"
- "我们的 SQL 跟 ttpos CountSale 接口对齐过，差额 < 0.01%"

老板问利润："Gross 还是 Operating？我们目前能算到 Gross，X 千万 THB，Gross Margin Y%"，
不要直接说"利润 X 元"——那是不专业的回答。

## 相关文档

- [pnl-statement-design.md](./pnl-statement-design.md) — P&L 入口设计稿（将基于本文档升级）
- [profit-margin-reconciliation-checklist.md](./profit-margin-reconciliation-checklist.md) — F vs G 对账方法论
- [profit-report-takeout-semantics.md](./profit-report-takeout-semantics.md) — 外卖口径调查
