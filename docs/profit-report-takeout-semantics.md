# 利润报表外卖口径调查归档

> **专门给"利润报表"（`profit_margin` / `profit_by_price`）的外卖部分**，跟
> `docs/takeout-revenue-calculation.md` 那份"营业额对账报表"是两条独立线，
> 不要混。归档日期 2026-05-13。

## TL;DR（下次别再纠结这两点）

1. **利润报表外卖口径跟 ttpos 后台"营业额/实收"页面在华莱士业务下数值恒等。** 真的对齐了，不是巧合到"几乎对得上"。8 家最活跃门店 2026-04 实测差额 = 0.00。
2. **平账（`sales_price == actual_amount`）不是 bug，是 ttpos 设计如此。** 华莱士目前没启用商家服务费/商家折扣 + 外卖订单 `tax = 0`，导致 ttpos 自己的"营业额"和"实收"也是同一个数。我们跟着相等是正确反映 ttpos 业务模型。

## 当前 BQ 利润报表的外卖口径

代码位置：`semantic/entities/takeout_line.py` + `semantic/entities/sale_event.py`（两份等价实现）。

```sql
sales_price   = SUM(IF(state ∈ {10,20,30,40}, toi.price * toi.quantity, 0))
actual_amount = SUM(IF(state ∈ {10,20,30,40}, toi.price * toi.quantity, 0))  -- 跟 sales_price 同表达式
refund/free/give/discount = 0
cancelled    = SUM(IF(state = 60, toi.price * toi.quantity, 0))
```

来源标注里写的是 `ttpos-server-go/main/app/repository/statistics_takeout.go:451-502` 的
`RankTakeoutProduct`（"商品排行"接口）。**不是** ttpos 主统计 `CountTakeoutSale`
那个 `platform_total` 口径。

## ttpos 后端有两套外卖口径，我们抄的是哪一套

`ttpos-server-go/main/app/repository/statistics_takeout.go` 里 6 个外卖统计函数分两类：

| 类别 | 函数 | 字段 | SUM 的是 |
|---|---|---|---|
| **主统计**（5 个） | CountTakeoutSale / ReceivedAmount / Payment / BusinessTimePeriod / ChannelSale | `platform_total` | 订单级 `platform_total` |
| **商品排行**（1 个） | RankTakeoutProduct | `sale_amount` | item 级 `toi.price * quantity` |

后台"外卖营业额/实收"页面（HTTP `GET /shop/statistics/business` → `service/business.go:432`
→ `service/statistics.go:141` → repo `CountTakeoutSale`）走的是**主统计**：

```sql
-- statistics_takeout.go:237-253
SUM(IF(state ∈ valid,    t.platform_total, 0))       AS total_sale_amount
SUM(IF(state ∈ business, t.platform_total, 0))       AS total_pay_amount
SUM(IF(state ∈ business, t.platform_total - t.tax, 0)) AS total_business_amount
```

其中 `platform_total` 在订单 converter 里固定公式（grab/lineman 一致）：

```
platform_total = subtotal + merchant_charge_fee − merchant_discount
```

## 为什么两个口径在华莱士数据下数值恒等

实测数据（2026-04，8 家最活跃外卖店）：

| shop | active orders | `Σ(toi.price × qty)` | `Σ platform_total` | 差额 | charge_fee≠0 | merch_disc≠0 | tax≠0 |
|---|---:|---:|---:|---:|---:|---:|---:|
| shop2598648160256000 | 1238 | 243,340.00 | 243,340.00 | 0.00 | 0 | 0 | 0 |
| shop4613872816128000 | 897 | 192,550.00 | 192,550.00 | 0.00 | 0 | 0 | 0 |
| shop2876210421760000 | 834 | 182,744.00 | 182,744.00 | 0.00 | 0 | 0 | 0 |
| shop5999171739648000 | 609 | 107,088.00 | 107,088.00 | 0.00 | 0 | 0 | 0 |
| shop3446618988544000 | 877 | 174,790.00 | 174,790.00 | 0.00 | 0 | 0 | 0 |
| shop4418766376960000 | 680 | 141,987.00 | 141,987.00 | 0.00 | 0 | 0 | 0 |
| shop6789240201216000 | 602 | 124,215.00 | 124,215.00 | 0.00 | 0 | 0 | 0 |
| shop1515821506560000 | 598 | 104,208.00 | 104,208.00 | 0.00 | 0 | 0 | 0 |

恒等三件套：

- `merchant_charge_fee` ≡ 0（华莱士没启用商家小单费/商家服务费）
- `merchant_discount` ≡ 0（没启用商家承担优惠）
- `tax` ≡ 0（泰国外卖订单不收税）

ttpos 公式退化：`platform_total = subtotal + 0 - 0 = subtotal`。
而 `subtotal` 在 LINE MAN converter 里被显式重算为 `Σ(item.total_price)`，在 Grab 里
直接取平台 payload（实测两者数值一致），所以 `subtotal = Σ(item.price × qty)`。
三种口径恒等。

> 数据验证脚本可在历史 commit / `scripts/` 里参照写法重跑——不是固化到代码里的常驻
> 健康检查，因此没单独放文件。要复跑直接对 `ttpos_takeout_order` +
> `ttpos_takeout_order_item` 写 SQL 即可。

## 平账（金额恒等式）的真相

外卖侧 `sales_price` 和 `actual_amount` 在 `takeout_line.py` 里被赋成**同一个表达式**，
所以金额恒等式：

```
sales_price = actual + refund + free + give + cancelled + discount
   Σpq[活]  =  Σpq[活]  +  0  +  0  +  0  +  0  +  0
```

数学恒等式 `x = x` 必然成立。这**不是**对账成功的证据，但**也不是**数据错误——
ttpos 本身就是这么设计的：

- `CountTakeoutSale` 的"营业额"= `platform_total - tax`
- "实收" = `platform_total`
- 华莱士 `tax = 0`，所以 ttpos 后台显示的"外卖营业额"和"外卖实收"也是同一个数

我们 BQ 报表两个字段相等 = 真实反映 ttpos 业务模型。

## 仍未解决（动手前要先想清楚）

### 1. 平台抽佣完全没采

`ttpos-server-go` 全仓库搜过，没有任何字段记录 Grab / LINE MAN / Shopee 对商家
的抽佣（commission / 平台手续费）。ttpos 把它定义在系统边界外。

**后果**：我们利润报表里外卖那部分的 `revenue` 是顾客付给平台的钱，**不是商家到手**。
利润 = revenue − cost 在外卖侧**系统性高估**抽佣比例那一块（通常 20-30%）。

**要算真实利润必须外接平台月度对账单** —— 这是产品决策，不只是技术工作。

### 2. 外卖侧没有"折扣明细"

外卖只有 `toi.price`（实付价），没有"标价 vs 折扣"的拆分。`toi.ttpos_price`
覆盖率 99.6% 可以做事后还原（`(ttpos_price - price) × qty` 是个候选的折扣指标），
但 ttpos 自己从来没用过这个字段做 SUM，没有"页面口径"可对齐。如果要拆，是我们
单方面的口径设计。

### 3. 隐性风险：业务一旦启用商家服务费/商家折扣，立刻偏离 ttpos 页面

恒等的前提是 `merchant_charge_fee = merchant_discount = 0`。一旦华莱士某店上线了：

- 商家小单费 → 我们的 `Σ(toi.price × qty)` < ttpos 页面的 `Σ platform_total`
- 商家承担优惠 → 我们的 `Σ(toi.price × qty)` > ttpos 页面的 `Σ platform_total`

未来对账失败时，第一反应应该是查这两个字段有没有变成非零。

### 4. 订单 converter 行为不对称（debug 时记住）

- **LINE MAN**：`subtotal` 在 `lineman_order_converter.go:215` 取平台值后，在
  `343-346` 被覆盖重算为 `Σ(item.total_price)`
- **Grab**：`subtotal` 直接取平台值
- **Grab `eater_payment`**：非现金支付时被 `subtotal` 覆盖（`grab_order_converter.go:219-227`），
  不能信
- **`ttpos_price`**：converter 阶段为空，service 层补，历史数据覆盖率 99.6%

## 相关代码 / 文档索引

- 我方实现：`semantic/entities/takeout_line.py`、`semantic/entities/sale_event.py`、
  `semantic/entities/total_line.py`、`semantic/validators/identities.py`
- ttpos 主统计：`ttpos-server-go/main/app/repository/statistics_takeout.go:200-286` (CountTakeoutSale)
- ttpos 商品排行：`ttpos-server-go/main/app/repository/statistics_takeout.go:451-502` (RankTakeoutProduct)
- ttpos 订单 converter：`main/app/modules/takeout/infrastructure/adapter/grab/grab_order_converter.go`、
  `.../lineman/lineman_order_converter.go`
- 营业额对账报表（独立线，不是利润报表）：`docs/takeout-revenue-calculation.md`
