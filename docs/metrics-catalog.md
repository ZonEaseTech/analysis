# 口径地图 — Metrics Catalog

> 每个核心指标的**唯一真源**：业务含义 / 公式 / SQL / 缓存 / 配置 / 对账锚 /
> 常见排障路径。
>
> 客户/财务/老板问"这个数怎么算的"，先翻这里。
> 改口径前先翻这里。
> 业界对应：dbt docs / Cube.dev metrics catalog / DataHub。

## 速查目录

| 类别 | 指标 |
|---|---|
| 销售域 一级节点 | [GMV](#gmv-总营业额) · [Net Sales](#net-sales-净销售额) · [Gross Profit](#gross-profit-销售毛利) |
| 销售域 拆分 | [Dine GMV](#dine_gmv-堂食营业额) · [Takeout GMV](#takeout_gmv-外卖营业额) · [COGS](#cogs-物料成本) |
| 销售域 损失项 | [Refund](#refund_amount-退款金额) · [Free/Give](#free_amount--give_amount-赠品赠送) · [Discount](#discount_amount-调价折扣) · [Cancelled](#cancelled_amount-外卖取消) |
| 结算域（估算） | [Platform Commission](#platform_commission-平台抽佣-估算) · [Contribution Margin](#contribution_margin-贡献毛利) |
| 财务域（待接入） | [Labor / Rent / Utilities / Marketing](#待接入指标-phase-3) · [Operating Income](#operating_income-经营利润) |
| KPI 比率 | [Gross Margin %](#gross-margin-毛利率) · [Food Cost %](#food-cost--食材成本率) · [Prime Cost %](#prime-cost--餐饮核心) · [AOV](#aov-客单价) · [Channel Mix](#channel-mix-渠道占比) · [Effective Take Rate](#effective-take-rate-抽佣率) |
| 元数据 / 审计 | [BOM Source](#bom_source-bom-来源) · [Price Source](#price_source-物料价来源) |

---

## GMV / 总营业额

**业务含义**：销售域总流水。商品按各渠道实际成交价 × 销量。**含**赠送/退款/折扣件，**不含**外卖取消订单。

**公式**：`堂食 GMV + 外卖 GMV`

**SQL 实现**：
- 堂食段：`semantic/entities/sale_line.py:38` — `SUM(product_sale_price * product_num)`
- 外卖段：`semantic/entities/takeout_line.py:28` — `SUM(IF(state IN (10,20,30,40), price * quantity, 0))`
- 合并：`semantic/entities/total_line.py:18-43` — `merged AS (FULL OUTER JOIN shop_sales + takeout_sales)`

**BQ 表**：`ttpos_statistics_product` + `ttpos_takeout_order_item`

**注意 / 排障**：
- 外卖侧 `toi.price * qty` 是商品级口径；ttpos 后端 `CountTakeoutSale` 用订单级 `platform_total`。
  华莱士现状（merchant_charge_fee = merchant_discount = 0）两者数值一致；上线商家服务费后会偏离。
- F vs G（标价×销量 vs 营业额）22 类因素分析见 [profit-margin-reconciliation-checklist.md](./profit-margin-reconciliation-checklist.md)。

**报表展示**：`pnl_statement` Sheet 1（总览）· Sheet 3（按店）· Sheet 4（按渠道）· Sheet 6（审计追溯）

---

## Net Sales / 净销售额

**业务含义**：扣完所有损失项后的"实收"。**等价于** ttpos 后端 `CountProductSale.actual_sale_amount`。是利润计算的分母锚点。

**公式**：`GMV − Returns − Cancellations − Promotions (Free + Give + Discount)`
即 `= ttpos actual_amount`（直接 SUM 各 entity 的 actual_amount 字段）

**SQL 实现**：
- 堂食段：`sale_line.py:42-43` — `SUM(IF(free|give, 0, final_price * (num - refund_num)))`
- 外卖段：`takeout_line.py:29` — 同 GMV 公式（外卖侧无折扣）
- 合并：`total_line.py:23-25` — `IFNULL(s.actual_amount, 0) + IFNULL(t.actual_amount, 0)`

**对账锚**：
- ✅ `TtposAnchorCheck` 验证 BQ Net Sales == ttpos 后端 SQL 结果，2026-04 实测差 69/3464 万 ≈ 0.0002%
- 实施：`semantic/reconciliation/checks/ttpos_anchor.py`

**财务级注意**：
- 当前是"管理会计 Net Sales"，**不等同于法定财报口径**（VAT 含税/不含税未确认）
- 法定财报 Net Sales 应该是不含税；ttpos 字段是否含税待财务确认
- 详见 [pnl-accounting-standards-gap.md](./pnl-accounting-standards-gap.md) Gap 3

---

## Gross Profit / 销售毛利

**业务含义**：扣完物料成本后的毛利。**不含**平台抽佣/人力/房租。

**公式**：`Net Sales − COGS`

**SQL/Python**：
- COGS 由 `bq_reports/pnl_statement.py::_compute_cogs_from_rows` 算
- 集团层在 `semantic/aggregations/pnl_layers.py::build_pnl` 减法

**Gross Margin %** = `Gross Profit / Net Sales`

**行业基准**：餐饮 60-70%（`semantic/aggregations/kpi_ratios.py::INDUSTRY_BENCHMARKS["gross_margin"]`）

**当前实测**：2026-04 华莱士 **5.1%** 🔴（远低于健康线，反映 ERP 单价虚高 + fallback_bom 误匹配等历史问题）

---

## dine_gmv / 堂食营业额

**业务含义**：仅 `channel='dine'` 的 GMV 部分。

**SQL 实现**：`semantic/entities/sale_event.py:23-54`（UNION ALL 的堂食段）
```sql
SELECT
  sp.product_package_uuid AS item_uuid,
  sp.product_sale_price AS price,
  'dine' AS channel,
  SUM(sp.product_num) AS qty,
  SUM(sp.product_sale_price * sp.product_num) AS sales_price,
  ...
FROM ttpos_statistics_product sp
GROUP BY item_uuid, price
```

**报表展示**：`pnl_statement.py::aggregate_sales_by_channel` 按 channel 拆 → Sheet 4

---

## takeout_gmv / 外卖营业额

**业务含义**：仅 `channel='takeout'` 的 GMV 部分（含 state=60 取消单的标价金额，但 GMV 一般用 active states 数据）。

**SQL 实现**：`semantic/entities/sale_event.py:56-91`（UNION ALL 的外卖段）

**注意**：
- 外卖侧 `toi.price` 是平台实付价（已含可能的促销让利）
- 商家结算价是订单级 `platform_total`（= subtotal + merchant_charge_fee − merchant_discount）
- 华莱士现状两者数值一致

---

## COGS / 物料成本

**业务含义**：跟着销量按 BOM 配方 × 物料单价算出来的食材成本。

**公式**：`Σ over SKU [ per_unit_cost × qty ]`
  其中 `per_unit_cost = Σ (bom_num × unit_price)` 跨 BOM 物料

**Python 实现**：`bq_reports/pnl_statement.py::_compute_cogs_from_rows`
- **单品**：`bom_num × unit_price × qty`
- **套餐**：遍历 `combo_structure` 子商品，BOM 摊薄 `bom_num × child_num × weight × qty`
- **fallback_bom 覆盖**：`_match_bom_layered(item_name, bom_layers)` 命中即 override BQ 原生
- **Resolver 单价**：`_resolve_unit_price_with_source` 走 priority 栈

**数据源**：
- BOM 数量：`ttpos_product_bom` (BQ 原生) + `bom_layers` (客户外挂)
- 物料单价：`price_layers` (客户成本表) + `uploaded_prices` + `ERPNext API` + `bq_native` priority 栈

**缓存位置**：
- `.cache/bq_reports/boms_v5_*.json` — BQ BOM
- `.cache/bq_reports/material_prices_v2_*.json` — 客户成本表
- `.cache/bq_reports/erpnext_prices_*.json` — ERPNext API

**配置位置**：`resources/wallace.YYYYMMDD/config.yaml`
- `bom_sources` — BOM 数量 priority 栈
- `material_price_sources` — 物料单价 priority 栈

**对账锚**：
- ✅ profit_margin 跟 pnl_statement COGS byte-equal（实测 2026-04 差 0.23/3287 万 ≈ 0 浮点累积）

**常见排障**：
- 单份成本异常高 → 八成是 fallback_bom 误匹配（如"香脆全鸡 → 香脆全鸡（半只）"，14 倍成本异常）
- 单份成本 = 0 → strict 模式 + 客户成本表没维护该物料
- 套餐成本 = 0 → combo_structure 没该 SKU 配方（删除或新加套餐）

---

## refund_amount / 退款金额

**业务含义**：堂食侧退款标价金额。**只有堂食有**，外卖侧固定 0。

**SQL 实现**：`sale_line.py:45` — `SUM(product_sale_price * refund_num)`

**对账规则**：进 `pnl_layers` 减项；进 `validators.AMOUNT_IDENTITY` 平账等式

---

## free_amount / give_amount / 赠品赠送

**业务含义**：
- `free_amount` — 赠品（公司活动赠送，进 free_num，整行金额计 0）
- `give_amount` — 赠送（顾客感谢/补偿赠送，进 give_num，整行金额计 0）
- ttpos 把整行算赠送，**不区分单件**

**SQL 实现**：
- `sale_line.py:50-51` — `SUM(IF(free_num > 0, sale_price * num, 0))`
- 外卖侧无此概念，固定 0

**对账规则**：进 `pnl_layers::promo_deductions` 减项

---

## discount_amount / 调价折扣

**业务含义**：堂食侧成交价低于标价的部分。`(product_sale_price − product_final_price) × (num − refund_num)`。

**SQL 实现**：`sale_line.py:53-54`

**对账规则**：进 `pnl_layers::promo_deductions` 减项

---

## cancelled_amount / 外卖取消

**业务含义**：外卖订单 state=60（取消）的标价金额。**只有外卖有**，堂食侧固定 0。

**SQL 实现**：`takeout_line.py:42` — `SUM(IF(state = 60, price * quantity, 0))`

---

## platform_commission / 平台抽佣 (估算)

**业务含义**：Grab/LINE MAN/Shopee 抽佣金额。**当前是估算值**（按 28% 默认率），待 Phase 2 接平台对账单升真值。

**Python 实现**：`pnl_layers.py::build_pnl` —
  `platform_commission = takeout_gmv × commission_rate_resolver.resolve("default")`

**Confidence**：`ESTIMATED`（Phase 2 升 `ACTUAL`）

**配置位置**：`resources/wallace.YYYYMMDD/resolvers.yaml::commission_rate`
- 通过 P3 fact_overrides 加载（`load_resolvers_from_yaml`）
- 不传 yaml 时走 `--commission-default-rate` CLI 参数（默认 0.28）

**对账锚（待 Phase 2）**：`PlatformPayoutCheck`（`semantic/reconciliation/checks/platform_payout.py`）
- 接 Grab/LINE MAN/Shopee 月度对账单 Excel → BQ 算的外卖营收 vs 商家结算 gross_sales
- 框架已就绪，loader 函数 `load_grab_statement` 等待客户给样本后实施

---

## contribution_margin / 贡献毛利

**业务含义**：扣完所有变动成本（COGS + 平台抽佣 + 配送费分担 + 支付通道费）后的毛利。回答"渠道层面赚不赚钱"。

**公式**：`Gross Profit − Platform Commission − Delivery Fee Share − Payment Processing`

**Python 实现**：`pnl_layers.py::build_pnl` `contribution_margin` 节点

**Confidence**：当前 `ESTIMATED`（依赖估算抽佣）

**关键发现**：2026-04 华莱士外卖 **CM% = -51%**（每卖 1 块亏 5 毛）— 见 `pnl_statement` Sheet 4

---

## 待接入指标 (Phase 3)

| 指标 | 来源 | 状态 |
|---|---|---|
| `labor` 人力成本 | HR / 工资系统 | ❌ N/A，待接入 |
| `rent` 房租 | 财务 ERP / 合同 | ❌ N/A |
| `utilities` 水电气 | 财务 ERP | ❌ N/A |
| `marketing` 营销 | 营销系统 / 财务 | ❌ N/A |

接入路径：通过 P3 `fact_overrides` 走 `resolvers.yaml::labor_cost` 等类别。详见 `skill: onboard-fact-table`。

---

## operating_income / 经营利润

**业务含义**：EBIT（Earnings Before Interest & Tax）。`Contribution Margin − Fixed OpEx (Rent + Labor + Utilities + Marketing)`。

**状态**：❌ 当前一律 N/A（依赖 Phase 3 固定成本接入）

---

## Gross Margin / 毛利率

**公式**：`Gross Profit / Net Sales`

**Python 实现**：`semantic/aggregations/kpi_ratios.py::compute_kpis`

**行业基准**：60-70%（餐饮 NRA 标准）

**当前实测**：5.1% 🔴 critical（远低于健康线）

---

## Food Cost % / 食材成本率

**公式**：`COGS / Net Sales`

**行业基准**：28-35%

**当前实测**：94.9% 🔴 critical（反映 ERP 单价虚高 + 部分 BOM 误匹配，**这是要业务侧治理的，不是 SQL bug**）

---

## Prime Cost % / 餐饮核心

**公式**：`(COGS + Labor) / Net Sales`

**行业基准**：55-65%（餐饮最关键运营指标）

**状态**：N/A（依赖 Phase 3 Labor 数据）

---

## AOV / 客单价

**公式**：`Net Sales / Order Count`

**当前实测**：~142.83 THB

**注意**：`order_count` 字段需要 sale_event SQL 单独 SUM，**目前用的是按 (item, price, channel) 拆出来的行数，不是订单数**。下一步可能要扩展 entity 加 order_count 字段。

---

## Channel Mix / 渠道占比

**公式**：
- `dine_mix = dine_sales / (dine_sales + takeout_sales)`
- `takeout_mix = takeout_sales / (...)`

**当前实测**：堂食 84.9% / 外卖 15.1%

---

## Effective Take Rate / 抽佣率

**公式**：`Platform Commission / Takeout Sales`

**行业基准**：20-30%（外卖平台抽佣范围）

**状态**：`ESTIMATED`（当前 = `commission_rate_resolver` 解析出的值，目前 28%）

---

## bom_source / BOM 来源

**业务含义**：审计列。标记某 SKU 的 BOM 数据来自哪个 priority 层。

**取值**：
- `bq_native` — BQ 原生 `ttpos_product_bom` 表
- `fallback_bom` 或 layer 名 — 客户外挂 BOM 表
- `无` — 该 SKU 没匹配到任何 BOM（**有销量则 SOURCE_COVERAGE identity 报警** 🔴）

**Python 实现**：`bq_reports/profit_margin_report.py::_annotate_agg_data_sources`

**报表展示**：`profit_margin.yaml` 列定义（BOM来源） + `pnl_statement` Sheet 6 审计

---

## price_source / 物料价来源

**业务含义**：审计列。标记物料单价来自哪个 priority 层。

**取值**（priority 高 → 低）：
- 客户外挂层名 (e.g. `market_20260513[taixi]`) — 客户成本表
- `uploaded_price_list` — `--price-list` 上传清单
- `ERPNext` — ERPNext API 价格
- `bq_native` — BQ ttpos_material 内置
- `无 (strict)` — strict 模式下客户成本表未命中（成本算 0，审计列标黄）
- `无` — 全栈未命中

**Python 实现**：`profit_margin_report.py::_resolve_unit_price_with_source`（内部走 `Resolver`）

---

## 排障速查

| 症状 | 看哪里 |
|---|---|
| 某 SKU 单份成本异常高（>500） | BOM 来源（看是否 fallback 误匹配） |
| 某 SKU 单份成本 = 0 | 价来源 = "无 (strict)"，要客户在成本表里维护该物料 |
| 总成本变化 > 20% | 跑 `git log` 看 BOM/price 配置改动；跑 `tests/test_resolver_parity.py` |
| BQ vs ttpos 差 > 0.001% | 看是否启用 merchant_charge_fee/merchant_discount |
| 月度毛利大幅波动 | 跑 `pnl_statement --compare-with` 看 Sheet 7 量/价/成本/结构归因 |
| 客户问"某指标怎么算" | 翻本文档对应章节 → 给 file:line 引用 |
| 数据缺失 (SOURCE_COVERAGE 报警) | console 输出 `[bom=无 | price=无]` 直接指源；联系客户补维护 |

## 相关文档

- [pnl-statement-design.md](./pnl-statement-design.md) — P&L 设计稿
- [pnl-primer-for-engineers.md](./pnl-primer-for-engineers.md) — 工程师视角财务 P&L 入门
- [pnl-accounting-standards-gap.md](./pnl-accounting-standards-gap.md) — 会计准则差异 / 财务对接
- [profit-margin-reconciliation-checklist.md](./profit-margin-reconciliation-checklist.md) — F vs G 22 类对账因素
- [profit-report-takeout-semantics.md](./profit-report-takeout-semantics.md) — 外卖口径调研归档
- [architecture-evolution-roadmap.md](./architecture-evolution-roadmap.md) — 整体演进路线
- [.claude/skills/onboard-fact-table/SKILL.md](../.claude/skills/onboard-fact-table/SKILL.md) — 接入新事实表 skill

## 维护规则

**改口径 = 改本文档**。代码改了忘改文档 → 文档变成谎言 → 不如不要。
强制 review：PR 改 `semantic/entities/*.py` / `semantic/aggregations/pnl_layers.py` 的，必须同步改本文档。
