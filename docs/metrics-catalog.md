# 口径地图 — Metrics Catalog

> ⚠️ **本文件由 `semantic/metrics/registry/*.yaml` 自动生成**
> (`semantic/metrics/render_catalog.py`)。**不要手改本文件。**
> 改口径 = 改 registry yaml → 跑 `venv/bin/python -m semantic.metrics.render_catalog`。
>
> 每个核心指标的唯一真源：业务含义 / 公式 / SQL / 来源 / 对账锚 / 置信度。
> 客户/财务/老板问"这个数怎么算的"，先翻这里。
> 业界对应：dbt Semantic Layer / Cube / DataHub / ODCS。


## 速查目录

| 业务域 | 指标 |
|---|---|
| 销售域 | [GMV / 总营业额](#gmv) · [Net Sales / 净销售额](#net-sales) · [Receivable / 营业应收](#receivable) · [Net Revenue / 门店实收](#net-revenue) · [Gross Profit / 销售毛利](#gross-profit) · [dine_gmv / 堂食营业额](#dine-gmv) · [takeout_gmv / 外卖营业额](#takeout-gmv) · [COGS / 物料成本](#cogs) · [Turnover / 营业额(成本表口径)](#turnover) · [Payment Collected / 支付净额(汇总表口径)](#payment-collected) · [Bank Deposited / 实际到账](#bank-deposited) · [refund_amount / 退款金额](#refund-amount) · [free_amount / 赠品金额](#free-amount) · [give_amount / 赠送金额](#give-amount) · [discount_amount / 调价折扣](#discount-amount) · [cancelled_amount / 外卖取消](#cancelled-amount) |
| 结算域 | [platform_commission / 平台抽佣（估算）](#platform-commission) · [contribution_margin / 贡献毛利](#contribution-margin) |
| 财务域 | [labor / 人力成本](#labor) · [rent / 房租](#rent) · [utilities / 水电气](#utilities) · [marketing / 营销](#marketing) · [operating_income / 经营利润](#operating-income) |
| KPI 比率 | [Gross Margin / 毛利率](#gross-margin) · [Food Cost % / 食材成本率](#food-cost) · [Prime Cost % / 餐饮核心](#prime-cost) · [AOV / 客单价](#aov) · [Channel Mix / 渠道占比](#channel-mix) · [Effective Take Rate / 抽佣率](#effective-take-rate) |
| 元数据 / 审计 | [bom_source / BOM 来源](#bom-source) · [price_source / 物料价来源](#price-source) |

---

<a id="gmv"></a>
## GMV / 总营业额

`销售域` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 粒度 `(item, price, channel)` · 单位 `THB`

**业务含义**：销售域总流水。商品按各渠道实际成交价 × 销量。含赠送/退款/折扣件，不含外卖取消订单。

**公式**：`堂食 GMV + 外卖 GMV`

**SQL 实现**：
- `semantic/entities/sale_line.py:38 — SUM(product_sale_price * product_num)`
- `semantic/entities/takeout_line.py:28 — SUM(IF(state IN (10,20,30,40), price * quantity, 0))`
- `semantic/entities/total_line.py:18-43 — merged AS (FULL OUTER JOIN shop_sales + takeout_sales)`

**数据来源**：源表 `ttpos_statistics_product`, `ttpos_takeout_order_item` · 上游指标 [dine_gmv](#dine-gmv), [takeout_gmv](#takeout-gmv)

**报表展示**：pnl_statement Sheet 1（总览）· Sheet 3（按店）· Sheet 4（按渠道）· Sheet 6（审计追溯）

**注意 / 排障**：外卖侧 toi.price * qty 是商品级口径；ttpos 后端 CountTakeoutSale 用订单级 platform_total。 华莱士现状（merchant_charge_fee = merchant_discount = 0）两者数值一致；上线商家服务费后会偏离。

**相关文档**：[profit-margin-reconciliation-checklist.md](./profit-margin-reconciliation-checklist.md)

---

<a id="net-sales"></a>
## Net Sales / 净销售额

`销售域` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 单位 `THB`

**业务含义**：扣完所有损失项后的实收。等价于 ttpos 后端 CountProductSale.actual_sale_amount，是利润计算的分母锚点。

**公式**：`GMV − Returns − Cancellations − Promotions(Free + Give + Discount) = ttpos actual_amount`

**SQL 实现**：
- `semantic/entities/sale_line.py:42-43 — SUM(IF(free|give, 0, final_price * (num - refund_num)))`
- `semantic/entities/takeout_line.py:29 — 同 GMV 公式（外卖侧无折扣）`
- `semantic/entities/total_line.py:23-25 — IFNULL(s.actual_amount,0) + IFNULL(t.actual_amount,0)`

**数据来源**：源表 `ttpos_statistics_product`, `ttpos_takeout_order_item` · 上游指标 [gmv](#gmv), [refund_amount](#refund-amount), [free_amount](#free-amount), [give_amount](#give-amount), [discount_amount](#discount-amount), [cancelled_amount](#cancelled-amount)

**对账锚**：TtposAnchorCheck — BQ Net Sales == ttpos 后端 SQL 结果（impl: `semantic/reconciliation/checks/ttpos_anchor.py`） — 2026-04 实测差 69/3464 万 ≈ 0.0002%

**注意 / 排障**：当前是管理会计 Net Sales，不等同法定财报口径（VAT 含税/不含税未确认）。详见 pnl-accounting-standards-gap.md Gap 3。 ⚠️ 标签有歧义: 此处实际是**应收 (receivable)** = ttpos actual_sale_amount，尚未扣除订单级 7 项营销折扣。 真·实收 (net_revenue) 需再减 order_discount。详见下方 receivable / net_revenue 拆分。

**相关文档**：[pnl-accounting-standards-gap.md](./pnl-accounting-standards-gap.md)

---

<a id="receivable"></a>
## Receivable / 营业应收

`销售域` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 粒度 `(item, channel)` · 单位 `THB`

**业务含义**：扣完行级折扣(调价 discount_amount)和退款/赠品/赠送后的净额，等于 ttpos CountProductSale.actual_sale_amount。 是"应收"，不等于"实收"——还没减订单级 7 项营销折扣。ttpos 后端 shop_statistics.go:274 CountProductSales 用 actual_sale_amount = IF(free|give, 0, final_price × (num − refund))。

**公式**：`GMV − refund_amount − free_amount − give_amount − discount_amount`

**SQL 实现**：
- `semantic/entities/sale_line.py:42-43 — IF(free_num|give_num, 0, product_final_price × (product_num − refund_num))`
- `semantic/entities/takeout_line.py:29 — 外卖侧 active states 的 platform_total`
- `semantic/entities/total_line.py:23-25 — IFNULL(s.actual_amount,0) + IFNULL(t.actual_amount,0)`

**数据来源**：源表 `ttpos_statistics_product`, `ttpos_takeout_order` · 上游指标 [gmv](#gmv), [refund_amount](#refund-amount), [free_amount](#free-amount), [give_amount](#give-amount), [discount_amount](#discount-amount)

**对账锚**：TtposAnchorCheck — BQ receivable == ttpos 后端 CountProductSale SQL 结果（impl: `semantic/reconciliation/checks/ttpos_anchor.py`） — 2026-04 实测差 69/3464 万 ≈ 0.0002%

**报表展示**：pnl_statement::Net Sales 行（实为应收）

**注意 / 排障**：对应 ttpos 后台"成本毛利"页的"营业额"列(cost_profit.go 算法) + 外卖 platform_total。 与实收(见 net_revenue)差 ≈ coupon + 活动 + 会员折扣 + 自定义折扣 + 抹零 + 积分抵扣(7 项订单营销折扣)。 bridge 脚本: scripts/adhoc/recon_cost_vs_summary_bridge.py。

---

<a id="net-revenue"></a>
## Net Revenue / 门店实收

`销售域` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 粒度 `(item, channel)` · 单位 `THB`

**业务含义**：receivable 再减 7 项订单营销折扣后的真·实收。对应 ttpos 后台"支付汇总"页口径: (payment_amount − refund_amount − payment_balance) + 外卖 platform_total。 是利润表的真分母。

**公式**：`receivable − Σ(coupon + member_discount + custom_discount + activity + gift + pay_points + zero_checkout)`

**SQL 实现**：
- `semantic/entities/order_discount.py:L28-31 — 7 项 DISCOUNT_EXPR`
- `semantic/entities/order_discount.py:L60 — CAST(ROUND(disc × rev/Σrev × 100) AS INT64)`

**数据来源**：源表 `ttpos_statistics_product`, `ttpos_sale_order`, `ttpos_takeout_order` · 上游指标 [receivable](#receivable)

**对账锚**：2026-06 桥已建(recon_cost_vs_summary_bridge.py), receivable − payment_collected ≈ coupon + other − refund_gap + takeout_gap, 残差抹零级 — 桥验证, 但未入闸门(待月跑自动锚)

**注意 / 排障**：ttpos 系统里没有"net_revenue"这张表; 它是 receivable(成本表) − 7 项订单折扣。 实际到账 (bank_deposited) 还需再减平台抽佣/退款时效差, 见 payment 域。

---

<a id="gross-profit"></a>
## Gross Profit / 销售毛利

`销售域` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 单位 `THB`

**业务含义**：扣完物料成本后的毛利。不含平台抽佣/人力/房租。

**公式**：`Net Sales − COGS`

**SQL 实现**：
- `bq_reports/pnl_statement.py::_compute_cogs_from_rows — COGS`
- `semantic/aggregations/pnl_layers.py::build_pnl — 集团层减法`

**数据来源**：上游指标 [net_sales](#net-sales), [cogs](#cogs)

**当前实测**：2026-04 华莱士毛利率 5.1% 🔴（远低健康线，反映 ERP 单价虚高 + fallback_bom 误匹配）

---

<a id="dine-gmv"></a>
## dine_gmv / 堂食营业额

`销售域` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 粒度 `(item, price, channel='dine')` · 单位 `THB`

**业务含义**：仅 channel='dine' 的 GMV 部分。

**公式**：`Σ(堂食 price × qty)`

**SQL 实现**：
- `semantic/entities/sale_event.py:23-54 — UNION ALL 的堂食段`

**数据来源**：源表 `ttpos_statistics_product`

**报表展示**：pnl_statement::aggregate_sales_by_channel 按 channel 拆 → Sheet 4

---

<a id="takeout-gmv"></a>
## takeout_gmv / 外卖营业额

`销售域` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 粒度 `(item, price, channel='takeout')` · 单位 `THB`

**业务含义**：仅 channel='takeout' 的 GMV 部分（含 state=60 取消单的标价金额，GMV 一般用 active states 数据）。

**公式**：`Σ(外卖 price × qty)`

**SQL 实现**：
- `semantic/entities/sale_event.py:56-91 — UNION ALL 的外卖段`

**数据来源**：源表 `ttpos_takeout_order_item`

**注意 / 排障**：外卖侧 toi.price 是平台实付价（已含可能的促销让利）；商家结算价是订单级 platform_total = subtotal + merchant_charge_fee − merchant_discount。华莱士现状两者数值一致。

---

<a id="cogs"></a>
## COGS / 物料成本

`销售域` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 单位 `THB`

**业务含义**：跟着销量按 BOM 配方 × 物料单价算出来的食材成本。

**公式**：`Σ over SKU [ per_unit_cost × qty ]，其中 per_unit_cost = Σ(bom_num × unit_price) 跨 BOM 物料`

**Excel**：`=SUMPRODUCT(消耗数量, 物料单价)`

**SQL 实现**：
- `bq_reports/pnl_statement.py::_compute_cogs_from_rows`

**数据来源**：源表 `ttpos_product_bom`, `ttpos_material`

**对账锚**：profit_margin 跟 pnl_statement COGS byte-equal — 2026-04 差 0.23/3287 万 ≈ 0 浮点累积

**当前实测**：Food Cost% 2026-04 = 94.9% 🔴（业务侧要治理，非 SQL bug）

**注意 / 排障**：单品 = bom_num × unit_price × qty；套餐遍历 combo_structure 子商品。BOM 数量源 ttpos_product_bom(BQ) + bom_layers(客户外挂)；物料单价走 price_layers > uploaded_prices > ERPNext > bq_native priority 栈。配置 resources/config.yaml 的 bom_sources / material_price_sources。 排障：单份成本异常高八成是 fallback_bom 误匹配；=0 多为 strict 模式未维护单价 / 套餐无配方。

**相关文档**：[profit-margin-reconciliation-checklist.md](./profit-margin-reconciliation-checklist.md)

---

<a id="turnover"></a>
## Turnover / 营业额(成本表口径)

`销售域` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 单位 `THB`

**业务含义**：ttpos 后台"成本毛利"页显示的营业额。堂食 = receivable(已扣行级折扣); 外卖 = subtotal(price×qty, 非 platform_total)。华莱士现状下外卖 sub≈platform_total(merchant_charge_fee=0)。

**公式**：`dine_receivable + takeout_subtotal`

**SQL 实现**：
- `semantic/entities/sale_line.py:42-43 — 堂食 final_price×(num−refund), 免单赠送=0`
- `recon_cost_vs_summary_bridge.py:34-37 — 堂食段: SUM(IF(free|give,0,product_final_price×(product_num−refund_num)))`
- `recon_cost_vs_summary_bridge.py:42-46 — 外卖段: SUM(IF(state IN(10,20,30,40),subtotal,0))`

**数据来源**：源表 `ttpos_statistics_product`, `ttpos_takeout_order` · 上游指标 [receivable](#receivable)

**对账锚**：recon_cost_vs_summary_bridge.py — turnover − payment_collected = coupon + other_disc − refund_gap + takeout_gap, 残差抹零级 — 2026-06 60店桥验证, 净残差抹零级

**报表展示**：ttpos 后台 → 成本毛利 → 统计 → 营业额列

**注意 / 排障**：营业额 ≠ 支付净额 ≠ 实际到账。客户拿营业额对银行会喊"数据错"——根因就三个口径混了。 见下方 payment_collected / bank_deposited 区分。

---

<a id="payment-collected"></a>
## Payment Collected / 支付净额(汇总表口径)

`销售域` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 单位 `THB`

**业务含义**：ttpos 后台"支付汇总"页的实收金额。堂食 = payment_amount − refund_amount − payment_balance (ttpos_statistics_sale 的实收, 扣了支付退款+挂账); 外卖 = platform_total(订单级结算价)。 是门店实际收到(但还没扣平台抽佣/银行手续费)的钱。

**公式**：`(payment_amount − refund_amount − payment_balance) + takeout platform_total`

**SQL 实现**：
- `recon_cost_vs_summary_bridge.py:38-40 — 堂食段: IFNULL(SUM(payment_amount−refund_amount−payment_balance),0)`
- `recon_cost_vs_summary_bridge.py:42-46 — 外卖段: SUM(platform_total) for active states`
- `semantic/entities/sale_event.py — 堂食 bill.payment_amount 仅含 POS 收款`

**数据来源**：源表 `ttpos_statistics_sale`, `ttpos_takeout_order`, `ttpos_sale_bill` · 上游指标 [turnover](#turnover)

**对账锚**：recon_cost_vs_summary_bridge.py — turnover−payment_collected ≈ 7项订单折扣 − 退款口径差 + 外卖口径差 — coupon 是主因; other(gift/pay_points) ≈0 未被样本检验(若某月搞整单赠送/积分抵扣可能双算)

**报表展示**：ttpos 后台 → 支付汇总 → 支付金额列

**注意 / 排障**：⚠ coverage gap: payment_amount 仅含堂食 POS 收款(sale_bill.payment_amount), 外卖支付走平台侧不经 sale_bill。 实测 shop001/005/010 payment_amount ≈ stat_actual_dine(缺口 ±0.2~2.5%)。外卖侧支付对账待接 Grab/LINE MAN 平台对账单(子项目 D)。

---

<a id="bank-deposited"></a>
## Bank Deposited / 实际到账

`销售域` · 状态 `待接入` · 置信度 `暂不可用（依赖未接入的数据源）` · 单位 `THB`

**业务含义**：银行账户实际入账金额。真源 = 银行流水 / 平台结算单(Grab/LINE MAN/Shopee), 不是 ttpos。payment_collected − 平台抽佣 − 支付通道费 − 退款时效差 − 结算周期差 ≈ bank_deposited。

**公式**：`payment_collected − platform_commission − payment_processing_fee ± timing_differences`

**SQL 实现**：
- `无 SQL 源 (bank statement / platform settlement CSV)`

**数据来源**：源表 `[`, `e`, `x`, `t`, `e`, `r`, `n`, `a`, `l`, `]`, ` `, `b`, `a`, `n`, `k`, ` `, `s`, `t`, `a`, `t`, `e`, `m`, `e`, `n`, `t`, ` `, `/`, ` `, `G`, `r`, `a`, `b`, `-`, `L`, `I`, `N`, `E`, `M`, `A`, `N`, `-`, `S`, `h`, `o`, `p`, `e`, `e`, ` `, `s`, `e`, `t`, `t`, `l`, `e`, `m`, `e`, `n`, `t`, ` `, `r`, `e`, `p`, `o`, `r`, `t` · 上游指标 [payment_collected](#payment-collected), [platform_commission](#platform-commission)

**对账锚**：待接平台对账单 loader → platform_payout anchor → bank reconciliation — Phase 2 待实施, 依赖客户给平台对账单样本

**注意 / 排障**：华莱士现状: 外卖占销售额 ~15%, 平台抽佣 28%, 结算周期 T+7~30 天。 当前 payment_collected 与 bank_deposited 的差 ≈ platform_commission(估算) + 结算时间差。 真·三大口径对齐: turnover − refunds/discounts = payment_collected − platform_fees = bank_deposited ± timing

---

<a id="refund-amount"></a>
## refund_amount / 退款金额

`销售域` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 单位 `THB`

**业务含义**：堂食侧退款标价金额。只有堂食有，外卖侧固定 0。

**公式**：`标价 × 退款数量`

**SQL 实现**：
- `semantic/entities/sale_line.py:45 — SUM(product_sale_price * refund_num)`

**数据来源**：源表 `ttpos_statistics_product`

**注意 / 排障**：进 pnl_layers 减项；进 validators.AMOUNT_IDENTITY 平账等式。

---

<a id="free-amount"></a>
## free_amount / 赠品金额

`销售域` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 单位 `THB`

**业务含义**：赠品（公司活动赠送，进 free_num，整行金额计 0）。ttpos 把整行算赠送，不区分单件。

**公式**：`Σ IF(free_num > 0, sale_price × num, 0)`

**SQL 实现**：
- `semantic/entities/sale_line.py:50 — free 段`

**数据来源**：源表 `ttpos_statistics_product`

**注意 / 排障**：外卖侧无此概念固定 0。进 pnl_layers::promo_deductions 减项。与 give_amount 在 catalog 同一节文档。

---

<a id="give-amount"></a>
## give_amount / 赠送金额

`销售域` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 单位 `THB`

**业务含义**：赠送（顾客感谢/补偿赠送，进 give_num，整行金额计 0）。

**公式**：`Σ IF(give_num > 0, sale_price × num, 0)`

**SQL 实现**：
- `semantic/entities/sale_line.py:51 — give 段`

**数据来源**：源表 `ttpos_statistics_product`

**注意 / 排障**：进 pnl_layers::promo_deductions 减项。

---

<a id="discount-amount"></a>
## discount_amount / 调价折扣

`销售域` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 单位 `THB`

**业务含义**：堂食侧成交价低于标价的部分。

**公式**：`(product_sale_price − product_final_price) × (num − refund_num)`

**SQL 实现**：
- `semantic/entities/sale_line.py:53-54`

**数据来源**：源表 `ttpos_statistics_product`

**注意 / 排障**：进 pnl_layers::promo_deductions 减项。

---

<a id="cancelled-amount"></a>
## cancelled_amount / 外卖取消

`销售域` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 单位 `THB`

**业务含义**：外卖订单 state=60（取消）的标价金额。只有外卖有，堂食侧固定 0。

**公式**：`Σ IF(state = 60, price × quantity, 0)`

**SQL 实现**：
- `semantic/entities/takeout_line.py:42`

**数据来源**：源表 `ttpos_takeout_order_item`

---

<a id="platform-commission"></a>
## platform_commission / 平台抽佣（估算）

`结算域` · 状态 `已上线（估算口径）` · 置信度 `估算（用默认率 / 待事实数据升真值）` · 单位 `THB`

**业务含义**：Grab/LINE MAN/Shopee 抽佣金额。当前是估算值（按 28% 默认率），待 Phase 2 接平台对账单升真值。

**公式**：`takeout_gmv × commission_rate`

**SQL 实现**：
- `semantic/aggregations/pnl_layers.py::build_pnl — commission_rate_resolver.resolve('default')`

**数据来源**：上游指标 [takeout_gmv](#takeout-gmv)

**对账锚**：PlatformPayoutCheck — Grab/LINE MAN/Shopee 月度对账单 vs BQ 算的外卖营收（impl: `semantic/reconciliation/checks/platform_payout.py`） — 框架已就绪，loader 等客户给样本后实施（Phase 2）

**注意 / 排障**：配置 resources/wallace.*/resolvers.yaml::commission_rate（P3 fact_overrides 加载）； 不传 yaml 时走 --commission-default-rate CLI 参数（默认 0.28）。

**相关文档**：[profit-report-takeout-semantics.md](./profit-report-takeout-semantics.md)

---

<a id="contribution-margin"></a>
## contribution_margin / 贡献毛利

`结算域` · 状态 `已上线（估算口径）` · 置信度 `估算（用默认率 / 待事实数据升真值）` · 单位 `THB`

**业务含义**：扣完所有变动成本（COGS + 平台抽佣 + 配送费分担 + 支付通道费）后的毛利，回答渠道层面赚不赚钱。

**公式**：`Gross Profit − Platform Commission − Delivery Fee Share − Payment Processing`

**SQL 实现**：
- `semantic/aggregations/pnl_layers.py::build_pnl — contribution_margin 节点`

**数据来源**：上游指标 [gross_profit](#gross-profit), [platform_commission](#platform-commission)

**当前实测**：2026-04 华莱士外卖 CM% = -51%（每卖 1 块亏 5 毛）— pnl_statement Sheet 4

---

<a id="labor"></a>
## labor / 人力成本

`财务域` · 状态 `待接入` · 置信度 `暂不可用（依赖未接入的数据源）` · 单位 `THB`

**业务含义**：人力/工资成本。待接入。

**公式**：`Σ 工资 + 社保 + 福利（来源 HR / 工资系统）`

**注意 / 排障**：接入路径 P3 fact_overrides 走 resolvers.yaml::labor_cost。详见 skill onboard-fact-table。

---

<a id="rent"></a>
## rent / 房租

`财务域` · 状态 `待接入` · 置信度 `暂不可用（依赖未接入的数据源）` · 单位 `THB`

**业务含义**：门店房租。待接入（财务 ERP / 合同）。

**公式**：`门店月租（来源财务 ERP / 合同）`

---

<a id="utilities"></a>
## utilities / 水电气

`财务域` · 状态 `待接入` · 置信度 `暂不可用（依赖未接入的数据源）` · 单位 `THB`

**业务含义**：水电气能耗成本。待接入（财务 ERP）。

**公式**：`水 + 电 + 气（来源财务 ERP）`

---

<a id="marketing"></a>
## marketing / 营销

`财务域` · 状态 `待接入` · 置信度 `暂不可用（依赖未接入的数据源）` · 单位 `THB`

**业务含义**：营销 / 推广费用。待接入（营销系统 / 财务）。

**公式**：`营销投放 + 推广（来源营销系统 / 财务）`

---

<a id="operating-income"></a>
## operating_income / 经营利润

`财务域` · 状态 `待接入` · 置信度 `暂不可用（依赖未接入的数据源）` · 单位 `THB`

**业务含义**：EBIT（Earnings Before Interest & Tax）。当前一律 N/A（依赖固定成本接入）。

**公式**：`Contribution Margin − Fixed OpEx(Rent + Labor + Utilities + Marketing)`

**数据来源**：上游指标 [contribution_margin](#contribution-margin), [labor](#labor), [rent](#rent), [utilities](#utilities), [marketing](#marketing)

---

<a id="gross-margin"></a>
## Gross Margin / 毛利率

`KPI 比率` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 单位 `%`

**业务含义**：销售毛利占净销售额的比例。

**公式**：`Gross Profit / Net Sales`

**Excel**：`=总毛利 / 实收`

**SQL 实现**：
- `semantic/aggregations/kpi_ratios.py::compute_kpis`

**数据来源**：上游指标 [gross_profit](#gross-profit), [net_sales](#net-sales)

**行业基准**：餐饮 60-70%（NRA 标准；kpi_ratios.INDUSTRY_BENCHMARKS['gross_margin']）

**当前实测**：2026-04 = 5.1% 🔴 critical

---

<a id="food-cost"></a>
## Food Cost % / 食材成本率

`KPI 比率` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 单位 `%`

**业务含义**：食材成本占净销售额的比例。

**公式**：`COGS / Net Sales`

**数据来源**：上游指标 [cogs](#cogs), [net_sales](#net-sales)

**行业基准**：28-35%

**当前实测**：2026-04 = 94.9% 🔴 critical（ERP 单价虚高 + 部分 BOM 误匹配，业务侧治理，非 SQL bug）

---

<a id="prime-cost"></a>
## Prime Cost % / 餐饮核心

`KPI 比率` · 状态 `待接入` · 置信度 `暂不可用（依赖未接入的数据源）` · 单位 `%`

**业务含义**：餐饮最关键运营指标。依赖 Phase 3 Labor 数据，当前 N/A。

**公式**：`(COGS + Labor) / Net Sales`

**数据来源**：上游指标 [cogs](#cogs), [labor](#labor), [net_sales](#net-sales)

**行业基准**：55-65%

---

<a id="aov"></a>
## AOV / 客单价

`KPI 比率` · 状态 `已上线` · 置信度 `估算（用默认率 / 待事实数据升真值）` · 单位 `THB`

**业务含义**：平均每单实收。

**公式**：`Net Sales / Order Count`

**数据来源**：上游指标 [net_sales](#net-sales)

**当前实测**：~142.83 THB

**注意 / 排障**：order_count 目前用按 (item, price, channel) 拆出来的行数，不是真订单数，故标 ESTIMATED。 下一步可能扩展 entity 加 order_count 字段。

---

<a id="channel-mix"></a>
## Channel Mix / 渠道占比

`KPI 比率` · 状态 `已上线` · 置信度 `真值（已对账 / 直接取数）` · 单位 `%`

**业务含义**：堂食 / 外卖销售额占比。

**公式**：`dine_mix = dine_sales / (dine_sales + takeout_sales)；takeout_mix 同理`

**数据来源**：上游指标 [dine_gmv](#dine-gmv), [takeout_gmv](#takeout-gmv)

**当前实测**：堂食 84.9% / 外卖 15.1%

---

<a id="effective-take-rate"></a>
## Effective Take Rate / 抽佣率

`KPI 比率` · 状态 `已上线（估算口径）` · 置信度 `估算（用默认率 / 待事实数据升真值）` · 单位 `%`

**业务含义**：平台抽佣占外卖销售额的比例。

**公式**：`Platform Commission / Takeout Sales`

**数据来源**：上游指标 [platform_commission](#platform-commission), [takeout_gmv](#takeout-gmv)

**行业基准**：20-30%

**当前实测**：= commission_rate_resolver 解析值，目前 28%

---

<a id="bom-source"></a>
## bom_source / BOM 来源

`元数据 / 审计` · 状态 `审计元数据列` · 置信度 `真值（已对账 / 直接取数）`

**业务含义**：审计列。标记某 SKU 的 BOM 数据来自哪个 priority 层。

**公式**：`取 priority 栈命中的层名：bq_native / 外挂层名 / 无`

**SQL 实现**：
- `bq_reports/profit_margin_report.py::_annotate_agg_data_sources`

**数据来源**：源表 `ttpos_product_bom`

**报表展示**：profit_margin.yaml（BOM来源列）+ pnl_statement Sheet 6 审计

**注意 / 排障**：无 = 该 SKU 没匹配到任何 BOM；有销量则 SOURCE_COVERAGE identity 报警 🔴。

---

<a id="price-source"></a>
## price_source / 物料价来源

`元数据 / 审计` · 状态 `审计元数据列` · 置信度 `真值（已对账 / 直接取数）`

**业务含义**：审计列。标记物料单价来自哪个 priority 层（跟 BOM 数量来源解耦）。

**公式**：`取 priority 栈命中层名（高→低）：客户外挂层 / uploaded_price_list / ERPNext / bq_native / 无(strict)`

**SQL 实现**：
- `bq_reports/profit_margin_report.py::_resolve_unit_price_with_source`

**数据来源**：源表 `ttpos_material`

**注意 / 排障**：多源用 ' + ' 拼接。无(strict) = strict 模式客户成本表未命中（成本算 0，审计列标黄）。

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

**改口径 = 改 `semantic/metrics/registry/*.yaml`**，然后跑
`venv/bin/python -m semantic.metrics.render_catalog` 重新生成本文件。
代码改了忘改 registry → 文档变谎言。强制 review：PR 改
`semantic/entities/*.py` / `semantic/aggregations/pnl_layers.py` 的，
必须同步改对应 registry 条目，并重新生成本文件（CI `--check` 会拦）。
