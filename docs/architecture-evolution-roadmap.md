# 平台架构演进路线图

> 基于 2026-05-13 讨论整合，把"5 阶段平台升级方案"与"财务 P&L 报表"统一到一份路线图。
> 状态：**设计稿，未实施**。决策点见末尾。

## 1. 当前状态 vs 终态

```
现在 (已落地)                                    终态 (P1-P5 全做完)
═══════════════════════════                    ════════════════════════════
semantic/                                       semantic/
├── entities/                                   ├── entities/        (不变)
├── dimensions/                                 ├── dimensions/      (不变)
├── aggregations/                               ├── aggregations/    (透传 source_map)
└── validators/                                 ├── resolvers/        ★ 多源裁决
                                                ├── validators/      (扩 identity 集)
utils/                                          ├── reconciliation/  ★ 跨系统对账
├── cache.py                                    ├── analytics/       ★ 差异分解
├── resource_adapter.py                         └── comparison/      ★ 跨期对比
└── report_engine.py                            
                                                utils/               (基本不变)
bq_reports/                                     ├── cache.py
├── profit_margin_report.py                     ├── resource_adapter.py
├── profit_by_price_report.py                   └── report_engine.py (财务格式扩展)
└── ...                                         
                                                bq_reports/
                                                ├── profit_margin_report.py    (受益)
                                                ├── profit_by_price_report.py  (受益)
                                                └── pnl_statement.py    ★ P&L 入口
                                                
                                                resources/
                                                ├── reports/*.yaml             (含 pnl)
                                                └── wallace.*/
                                                    ├── config.yaml
                                                    ├── resolvers.yaml      ★
                                                    ├── combo_overrides.yaml★
                                                    └── fact_overrides/     ★
```

## 2. 整合后的 6 阶段路线

| 阶段 | 目标 | 工作量 | 累计 | 独立可发布？ | 类别 |
|---|---|---:|---:|---|---|
| **P1** | Resolver 基础设施 + BOM 收编 | 1.5 天 | 1.5 | ✅ 双跑对账后合并 | 平台 |
| **P2** | Source 写进 Validator (来源闭环) | 1.0 天 | 2.5 | ✅ | 平台 |
| **P3** | Combo + 通用 fact_overrides 入口 | 1.0 天 | 3.5 | ✅ | 平台 |
| **P3.5** ★ | **财务 P&L 报表入口**（基于 P1+P2+P3）| 1.5 天 | 5.0 | ✅ | 应用 |
| **P4** | 跨系统对账层 (ERPNext/财务/POS 三方) | 2-3 天 | 7-8 | ✅ | 平台 |
| **P5** | 差异分解 + 期间对比 | 2.0 天 | 9-10 | ✅ | 平台 |

> **P3.5 是 5 阶段方案合并我之前的 P&L 工作而来**——把"老板要 P&L"的应用层需求放在 P1+P2+P3 之后做，
> 这样 P&L 报表的每个数字**自带 source 元数据 + 可走 fact_overrides 调账**，
> 比独立做 P&L 多 0.5 天，但可信度高一个数量级。

## 3. 每阶段详解

### P1 — Resolver 基础设施 + BOM 收编（1.5 天）

**目标**：把现在"只 BOM 有"的层叠机制抽成通用基础设施。**零行为变化**——纯重构 + 双跑对账。

**新增**：
```
semantic/resolvers/
├── base.py        # Resolver, Resolved, Provider Protocol
├── providers.py   # DictProvider, YamlMatchProvider, CallableProvider
└── builder.py     # 从 yaml 配置构造 Resolver
```

**改动**：
- `_bom_for_item` 签名从 9 个参数收敛到 3 个（`(item_uuid, store_num, resolver)`)
- `bom_layers / uploaded_prices / erp_prices` 三个来源都封装成 Provider

**验证**：
- 跑同一月份新旧版本，Excel 必须**逐单元格 diff = 0**
- 用 `diff_excel.py` 脚本自动比对

**架构影响**：`+semantic/resolvers/`

**为什么必做**：是 P2-P5 的地基。不做 P1，后面 4 个阶段都做不了。

---

### P2 — Source 写进 Validator（1 天）

**目标**：traceId 联动。来源审计不再只写 Excel，**让 validator 也消费它**，闭环。

**新增**：
```python
# semantic/validators/identities.py
SOURCE_COVERAGE_IDENTITIES = [
    "bom_source_must_not_be_missing",     # 有销量但 BOM 来源 = "无" → 🔴
    "price_source_must_not_be_missing",   # 物料缺单价来源 → 🔴
]

SANITY_BAND_IDENTITIES = [
    "food_cost_pct_in_band",              # 食材率 25-40% → 🟡 越界
    "combo_cost_le_components_sum",       # 套餐成本 ≤ 单品和 → 🔴 违反
    "refund_ratio_under_5pct",            # 退款率 <5% → 🟡 超过
    "free_qty_ratio_under_10pct",         # 赠送率 <10% → 🟡 超过
]
```

**改动**：
- aggregations 输出的 row 增加 `_source_map: {field: provider_name}` 字段
- validator 失败信息附 source_map → console 输出 "差 ¥20 在 revenue@market_20260513 这条源"

**架构影响**：
- `semantic/validators/`（扩 identity 集，不动 core）
- `semantic/aggregations/`（row 透传 source_map）

**收益**：
- 客户/老板拿 Excel 之前，**console 已经把"哪条源 + 哪 SKU + 多大偏差"打印出来了**
- 现有散装 sanity check（单份成本=0 等）收编进 validator 体系（一致的三级 severity）

**为什么最高 ROI**：把现有"做完导出再核查"的事前移到"导出前自动揪问题"。

---

### P3 — Combo override + 通用 fact_overrides 入口（1 天）

**目标**：真正解决"市场扔新事实表 = 改代码"。

**新增**：
```yaml
# resources/wallace.*/resolvers.yaml
resolvers:
  bom: [...]                    # P1 已建
  price: [...]                  # P1 已建
  combo_structure:              # ★ 新增 override 通道
    - { priority: 100, kind: yaml, path: ./combo_overrides.yaml }
    - { priority: 50,  kind: bq,   sql: ... }

  fact_overrides:               # ★ 通用入口
    revenue_adjustment: [...]   # 营收手工调账
    free_qty_supplement: [...]  # 赠送补录
    store_attribute: [...]      # 门店属性
    # 市场以后扔什么新类别就在这开新条目
```

**新动作 SOP**（市场扔新表）：
1. 给类别命名 → 写 resource_adapter 解析配置
2. `resolvers.yaml` 加 4 行注册
3. 报表脚本"接受哪些类别调整"白名单加 1 行（业务安全边界）
4. 重跑

**整个流程不动 Python 逻辑代码**（仅白名单是一行业务声明）。

**架构影响**：不增子层，是 P1 基础设施的应用扩展。

**为什么必做**：客户/市场每月都在扔新表，每次改代码会持续吃工时。

---

### P3.5 — 财务 P&L 报表入口（基于 P1+P2+P3，1.5 天）★ 整合

**目标**：把"老板要 P&L"应用需求兜住，**复用 P1+P2+P3 的所有基础设施**。

**新增**：
```
bq_reports/
└── pnl_statement.py                  # 主入口

semantic/aggregations/
├── pnl_layers.py                     # P&L 各层聚合 (基于 P1 的 source_map)
└── kpi_ratios.py                     # 比率库 (Gross Margin / Food Cost / AOV)

resources/reports/
└── pnl_statement.yaml                # 5 个 sheet 列定义

tests/
├── test_pnl_layers.py
├── test_kpi_ratios.py
└── test_pnl_statement_smoke.py
```

**5 个 Sheet 结构**（详见 [pnl-statement-design.md](./pnl-statement-design.md)）：
1. 集团损益表（标准 P&L 分层）
2. KPI Dashboard（关键比率 + 行业基准）
3. 按店损益
4. 按渠道损益对比
5. 数据来源审计（**自动**来自 P2 的 source_map）

**P1+P2+P3 给 P3.5 带来的能力**：

| 能力 | 来源 |
|---|---|
| P&L 每个数字带 source（哪个 BQ 表/CTE/Provider）| P1 Resolver + P2 source_map |
| 平台抽佣估算可通过 `fact_overrides.commission_rate` YAML 配置调整 | P3 fact_overrides |
| 人力/房租通过 `fact_overrides.labor_cost` 接入（Phase 3 真值前手工录入）| P3 fact_overrides |
| 财务校验（Net Sales = ttpos actual_amount）走 validator 闭环 | P2 |

**架构影响**：
- `+bq_reports/pnl_statement.py`
- `+semantic/aggregations/pnl_layers.py` + `kpi_ratios.py`
- `+resources/reports/pnl_statement.yaml`

**为什么放 P3 之后**：P&L 是给老板看的对外产物，必须建立在可信的数据基础上。

---

### P4 — 跨系统对账层（2-3 天，Tier 3 落地）

**目标**：业界金标准之一——双路径对账。从"内部自洽"升级到"跟外部系统对得上"。

**新增子层**：
```
semantic/reconciliation/
├── base.py              # ReconciliationCheck 抽象
├── sources/
│   ├── erpnext.py       # 拉 ERPNext 物料出库总量
│   ├── finance_gl.py    # 拉财务总账营收
│   ├── platform_payout.py  # ★ 平台对账单 (Grab/LINE MAN/Shopee)
│   └── pos_daily.py     # 拉 POS 日结
└── checks/
    ├── material_consumption.py   # BQ 销售消耗 vs ERP 出库
    ├── revenue_tie_out.py        # BQ 营收 vs 财务凭证
    ├── takeout_payout_tie_out.py # ★ BQ 外卖营收 vs 平台对账单
    └── refund_tie_out.py         # BQ 退款 vs 财务退款
```

**输出**：月度对账报告，每条对账线给出差异 + 严重度 + 嫌疑名单。

```
================ 跨系统对账 2026-05 ================
✅ 营收对账        BQ ¥1,234,567 vs 财务 ¥1,234,520  差 ¥47  (<0.01%)
🔴 物料消耗对账    BQ 计算 vs ERP 出库 差 12%
   嫌疑 BOM: MAT001 (差 35%), MAT042 (差 18%), ...
🟡 退款对账        BQ ¥3,200 vs 财务 ¥3,250  差 ¥50 (1.5%)
🔴 外卖结算对账    BQ ¥456,789 vs Grab 对账单 ¥320,000  差 ¥136,789
   原因：平台抽佣未在 BQ 反映（差异 ≈ 30% 抽佣率）
```

**P3.5 反向受益**：P4 完成后，P3.5 的 P&L 报表里"平台抽佣"那一行从**估算值**自动升级为**真值**，
"Net Sales" 那一行加上"跟财务凭证差 ¥X"的对账锚。

**前置条件**：
- ERPNext API 访问 ✅ 你已有
- **平台对账单 Excel 月度导出** ⚠️ 客户/财务提供
- 财务总账数据源 ⚠️ 需要确认是否可得

**架构影响**：`+semantic/reconciliation/`

**决策点**：看数据可得性。能拿到财务凭证 → 必做；只拿到平台对账单也值得做（外卖真实利润关键）。

---

### P5 — 差异分解 + 期间对比（2 天，战略级）

**目标**：客户问"为什么这个月毛利掉了 ¥2k"，机器**自动**给答案。

**新增子层**：
```
semantic/analytics/
├── variance_decomposition.py    # 量差/价差/成本差/结构差分解
└── period_comparison.py         # 期间对比框架（MoM/YoY 通用）
```

**输出**：
```
================ 毛利差异分解 2026-05 vs 2026-04 ================
总差异: ¥-2,103.50

量差: ¥-450    (销量同比 -3.2%, 影响 SKU: 超值套餐 4/7)
价差: ¥+120    (改价提价 +1.5%)
成本差: ¥-1,680  ← 主要 ⚠️
   └─ 食材成本上涨 (MAT001 ¥0.5→¥0.7) 影响 ¥-1,200
   └─ Combo BOM 口径变化（weight 摊薄修正）影响 ¥-480
结构差: ¥-93   (高毛利套餐占比下降 1.2pp)
```

**P3.5 反向受益**：P&L 报表加一个 Sheet 6 "本月差异分解"，老板打开就能看到"为什么变了"。

**架构影响**：`+semantic/analytics/`

**决策点**：客户/老板对"为什么变了"问得频不频。

---

## 4. 依赖关系图

```
              P1 (Resolver)
              │
              ├──── P2 (Source 闭环)
              │     │
              │     └─── P3 (fact_overrides)
              │           │
              │           └─── P3.5 (P&L 报表) ★ 应用层
              │                 │
              ▼                 │
            P4 (跨系统对账) ────┤    ← P4 提供真实数据给 P3.5
                                │
                          P5 (差异分解) ★ P&L 加 Sheet 6
```

**关键观察**：
- P1 是所有后续阶段的地基
- P3.5 (P&L) 依赖 P3，**不依赖** P4/P5；P4/P5 完成后 P3.5 自动升级
- P4 不依赖 P5，反之亦然

## 5. 跟现有报表的关系

| 现有报表 | P1 后受益 | P2 后受益 | P3 后受益 | P4 后受益 | P5 后受益 |
|---|---|---|---|---|---|
| `profit_margin` | 单价/BOM 来源统一 | source 出问题立刻报 | 调价/赠送可 YAML 录入 | 跟 ERP 对账 | 跨期变化解释 |
| `profit_by_price` | 同上 | 同上 | 同上 | 同上 | 同上 |
| **`pnl_statement`** | (P3.5 引入) | (P3.5 引入) | (P3.5 引入) | 抽佣升级为真值 | 自动差异解释 |
| 销售业绩/套餐报表 | 同上 | 同上 | 同上 | (不需要) | (不需要) |

**所有现有报表在 P1+P2 完成后都自动受益**——这就是平台思维 vs 报表思维的区别（参考
[wallace_project_is_a_platform](../.claude/projects/-home-weifashi-hwt-analysis/memory/wallace_project_is_a_platform.md)）。

## 6. 外部数据依赖清单

| 阶段 | 需要的外部数据 | 来源 | 当前状态 |
|---|---|---|---|
| P1-P3 | 无 | — | ✅ 可立即开始 |
| P3.5 | 无（抽佣走估算）| — | ✅ 可立即开始 |
| **P4** | **Grab/LINE MAN/Shopee 月度对账单 Excel** | 客户/财务从平台后台导出 | ⚠️ **待确认** |
| **P4** | **财务总账 / ERP 凭证** | 客户财务系统 | ⚠️ **待确认** |
| P4 | ERPNext 物料出库 | ERPNext API | ✅ 已对接 |
| P5 | 历史月度数据 | BQ 自有 | ✅ 数据有 |
| 未来 | 人力成本（工资单/HR）| 客户 HR | ⚠️ 长期目标 |
| 未来 | 房租 / 水电 | 客户财务 | ⚠️ 长期目标 |

**关键依赖**：P4 卡在"客户能否提供平台对账单 + 财务凭证"。这是组织协调问题，不是技术问题。

## 7. 客户/老板交付物对照

| 阶段完成 | 能跟客户/老板说什么 |
|---|---|
| P1 | "我们重构了数据来源体系，无行为变化（diff=0）" |
| P2 | "数据有问题，console 会自动报警，附问题源；过去靠手工核查的事现在自动" |
| P3 | "市场扔补录表给我们，4 行 YAML 接进来，不用改代码" |
| **P3.5** | **"给老板的 P&L 损益表上线，5 个 sheet，含 KPI Dashboard 和菜单工程矩阵"** |
| P4 | "BQ 算的 vs 财务/平台/ERP 真账，每月自动对账报告，差异 < ¥X" |
| P5 | "毛利跌了 ¥2k，机器告诉你是量、价、成本、结构哪一项导致" |

## 8. 推荐执行节奏

**第一波（2-3 天）**：P1 + P2
- 核心地基 + 来源闭环
- 风险最低、ROI 最高

**第二波（2 天）**：P3 + P3.5
- 把市场扔表的痛点解决
- 顺便交付老板要的 P&L

**第三波（2-3 天，需外部数据）**：P4
- 看客户能不能给平台对账单 / 财务凭证
- 拿不到只做 ERPNext 部分也值

**第四波（2 天，按需）**：P5
- 客户问"为什么变了"频繁就做

**最小可行**：P1+P2 = **2.5 天**
**强烈推荐止步点**：P1+P2+P3+P3.5 = **5 天**（地基 + 应用一体）
**金标准**：全做 = **9-10 天**

## 9. 已归档的相关文档

| 文档 | 跟本路线图的关系 |
|---|---|
| [profit-report-takeout-semantics.md](./profit-report-takeout-semantics.md) | P&L 数据口径基础 |
| [profit-margin-reconciliation-checklist.md](./profit-margin-reconciliation-checklist.md) | P4 对账方法论基础 |
| [pnl-statement-design.md](./pnl-statement-design.md) | P3.5 详细设计 |
| [pnl-primer-for-engineers.md](./pnl-primer-for-engineers.md) | P3.5 财务知识背景 |
| memory/[wallace_project_is_a_platform.md](../.claude/projects/-home-weifashi-hwt-analysis/memory/wallace_project_is_a_platform.md) | 平台定位认知 |

## 10. 待决策点（实施前需用户拍板）

### 全局决策

1. **整体路线是否接受 6 阶段方案**？还是要再调整？
2. **执行节奏**：连贯做完 P1-P3.5（5 天），还是分批？

### P1 决策

3. **diff 验证策略**：双跑对比工具用现有还是新写？逐单元格 diff 还是抽样？

### P2 决策

4. **sanity band 阈值**：食材率 25-40%、退款率 <5%、赠送率 <10% —— 是否符合华莱士实际？

### P3 决策

5. **fact_overrides 业务白名单**：哪些类别允许 override？建议起步只开 commission_rate / labor_cost / store_attribute 三类，避免乱改。
6. **新动作 SOP**：谁有权改 `resolvers.yaml`？走什么 review 流程？

### P3.5 决策

7. **抽佣率估算**：Grab 30% / LINE MAN 25% / Shopee 20% —— 用业内通用值还是问客户/财务要更精确的？
8. **是否单独做 KPI Dashboard Sheet**：要还是合到主 P&L sheet？
9. **是否做菜单工程矩阵 Sheet 4**：要还是 Phase 2 做？

### P4 决策（待外部数据可得性）

10. **平台对账单格式**：手动 Excel 上传 / 客户写脚本拉 / 我们对接平台 API？三种成本差很大
11. **财务凭证拿不到怎么办**：只做 ERPNext + 平台对账 那部分？
12. **对账频率**：月度还是每日？月度足够吗？

### P5 决策

13. **差异分解维度**：量/价/成本/结构 四维够吗？要不要加渠道维度（堂食 vs 外卖）？
14. **是否做 BoM 变更告警**：BOM 改动跟成本差关联，要不要单独提示？

## 11. 风险

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| P1 双跑 diff ≠ 0 | 中 | 重构延期 | 写自动 diff 工具，差就排查；最坏回滚 |
| P2 validator 误报太多 | 中 | 噪音淹没信号 | sanity band 阈值要分店调，不一刀切 |
| P3 fact_overrides 被滥用 | 低 | 数据可信度下降 | 业务白名单 + review SOP |
| P3.5 跟现有 profit_margin 重复 | 中 | 客户疑惑用哪个 | 文档说清楚定位差异（详见 P3.5 章节） |
| P4 拿不到外部数据 | 高 | P4 卡住或缩减 | 先做 P1-P3.5，P4 跟客户并行谈数据源 |
| P5 客户不关心差异分解 | 中 | 投入回报低 | P3.5 完成后看客户反馈再决定 |

---

## 12. 下一步

如果整体路线接受，进入 **PMA 三阶段** ：

```
docs/plan/
├── investigate-P1-resolver.md       # 调研当前 BOM 解析现状 + 设计 Provider Protocol
├── proposal-P1-resolver.md          # 详细实施方案（含 diff 验证策略）
└── implement-P1-resolver.md         # 实施记录（边做边写）
```

每个 P 阶段一套 investigate/proposal/implement，按 PMA 流程走。

实施前**还可以再压缩**——如果有某个阶段你觉得不必要，可以从路线里删掉。
