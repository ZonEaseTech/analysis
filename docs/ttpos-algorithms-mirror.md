# ttpos 后端统计算法 — 镜像与对照

> 把 ttpos 后端**每个统计接口的算法/口径**镜像一份到本仓库，作为契约附件。
>
> 跟 `data-contract.md` 互补：那个是"字段使用清单"（用了哪些），本文档是"算法
> 镜像"（怎么算的）。
>
> 用途：
> 1. 客户问"为什么我们的数字跟 ttpos 后台某接口一致/不一致"——翻这里
> 2. ttpos 后端升级时，知道我们 mirror 的算法跟着改没改
> 3. 报表服务作为独立 product 时，这份是合约附件（"参考了上游什么算法"）
> 4. 新人/AI 助手做调研——一份文档看清"ttpos 怎么算 → 我们怎么实现 → 差额多少"
>
> 归档日期 2026-05-13。

## 0. 全景 (完整 37 个统计函数)

ttpos 后端有 **37 个统计/排行/导出函数**（堂食 25 + 外卖 12），我们 mirror 了
**~3 个核心算法**。mirror 率 ~8%——不是漏，是**按需 mirror**：客户/老板真用到
才补 mirror，否则保持 gap 减负担。

### 0.1 堂食侧 `repository/statistics.go` (25 个统计函数)

#### 销售总览类 (3 个)

| ttpos 接口 | Go 源码:行 | mirror? | 我们实现 / Gap 价值 |
|---|---|:---:|---|
| `CountSale` | `statistics.go:187-250` | ⚠️ 部分 | `total_line.py merged_cte` (仅合并部分, 没扣 tax) |
| `CountSaleDays` | `statistics.go:251-672` | ❌ | 日级销售; 我们目前月度聚合, 接日报时补 |
| `Count7Days` | `statistics.go:1389-1419` | ❌ | 7 天滚动; 看板/趋势用 |

#### 商品销售类 (3 个 - 含核心 mirror)

| ttpos 接口 | Go 源码:行 | mirror? | 我们实现 / Gap 价值 |
|---|---|:---:|---|
| `CountProduct` | `statistics.go:979-1042` | ❌ | 商品销售汇总 (用 RankProduct 替代) |
| **`CountProductSale`** ⭐ | `statistics.go:1935-2237` | ✅ **完全 mirror** | `sale_line.py:42-43` (核心实收算法, byte-equal) |
| `CountPackageDetailProductSale` | `statistics.go:2238-2628` | ❌ | 套餐详情销售; 做套餐成本分析时补 |

#### 排行类 (1 个)

| ttpos 接口 | Go 源码:行 | mirror? | 我们实现 / Gap 价值 |
|---|---|:---:|---|
| `RankProduct` | `statistics.go:1234-1356` | ❌ | top10 排行 (用 `refund_time=0` 过滤, 跟 CountProductSale 算法不同); 我们用 CountProductSale 算法, 不实现排行 |

#### 维度类 (4 个)

| ttpos 接口 | Go 源码:行 | mirror? | 我们实现 / Gap 价值 |
|---|---|:---:|---|
| `CountCategory` | `statistics.go:906-978` | ❌ | 按品类销售; data-menu 标 ⚠️, 加 `pp.category_uuid` 就能补 |
| `CountArea` | `statistics.go:1043-1086` | ❌ | 按区域销售; 多店分区时补 |
| `CountAreaDays` | `statistics.go:1087-1233` | ❌ | 按区域日销 |
| `CountChannelSale` | `statistics.go:3612-3981` | ❌ | 渠道销售; 我们 P&L Sheet 4 类似覆盖 |

#### 支付类 (5 个)

| ttpos 接口 | Go 源码:行 | mirror? | 我们实现 / Gap 价值 |
|---|---|:---:|---|
| `CountPayment` | `statistics.go:673-713` | ❌ | 支付方式销售; "现金 vs 卡 vs 扫码" 分析时补 |
| `CountPaymentDays` | `statistics.go:714-845` | ❌ | 日级支付 |
| `CountFreePayment` | `statistics.go:2629-2648` | ❌ | 免费支付 (赠送统计) |
| `CountFreePaymentDays` | `statistics.go:2649-2670` | ❌ | 日级 |
| `CountBusinessPaymentMethod` | `statistics.go:3302-3611` | ❌ | 业务支付方式拆分 |

#### 税务类 (3 个)

| ttpos 接口 | Go 源码:行 | mirror? | 我们实现 / Gap 价值 |
|---|---|:---:|---|
| `CountTax` | `statistics.go:846-865` | ❌ | 税额; **法定财报必需** (data-menu 标 ❌) |
| `CountBuffetTax` | `statistics.go:866-885` | ❌ | 自助餐税 |
| `CountBuffetDelayTax` | `statistics.go:886-905` | ❌ | 自助餐延迟税 |

#### 会员类 (5 个)

| ttpos 接口 | Go 源码:行 | mirror? | 我们实现 / Gap 价值 |
|---|---|:---:|---|
| `CountMemberNum` | `statistics.go:1420-1437` | ❌ | 会员数; data-menu 已声明 "❌ ttpos 不采顾客信息"——但成员数还是有 |
| `CountMemberNumDays` | `statistics.go:1438-1495` | ❌ | 日级 |
| `CountMember` | `statistics.go:1602-1629` | ❌ | 会员消费统计 |
| `CountMemberDays` | `statistics.go:1630-1757` | ❌ | 日级 |
| `CountMemberPayment` | `statistics.go:1758-1794` | ❌ | 会员支付方式 |
| `CountMemberPaymentDays` | `statistics.go:1795-1934` | ❌ | 日级 |

#### 异常/退款类 (3 个)

| ttpos 接口 | Go 源码:行 | mirror? | 我们实现 / Gap 价值 |
|---|---|:---:|---|
| `CountUnpaidOrder` | `statistics.go:1546-1601` | ❌ | 未付款订单 |
| `CountCancelOrder` | `statistics.go:2671-2700` | ❌ | 取消订单; 数据健康监测时补 |
| `CountRefundSummary` | `statistics.go:3982-end` | ❌ | 退款摘要; 客户问退款明细时补 |

#### 业务时段类 (2 个)

| ttpos 接口 | Go 源码:行 | mirror? | 我们实现 / Gap 价值 |
|---|---|:---:|---|
| `CountBusinessTimePeriod` | `statistics.go:2739-2766` | ❌ | 时段分析 (Daypart); 业界餐饮标配, 我们暂未做 |
| `CountBusinessSummary` | `statistics.go:2956-3301` | ❌ | 业务摘要; 类似我们 pnl Sheet 1 但更精简 |

---

### 0.2 外卖侧 `repository/statistics_takeout.go` (12 个)

| ttpos 接口 | Go 源码:行 | mirror? | 我们实现 / Gap 价值 |
|---|---|:---:|---|
| `CountTakeoutSale` (主统计) | `statistics_takeout.go:200-298` | ⚠️ 仅对账锚 | `ttpos_anchor.py::TTPOS_NET_SALES_SQL` (差异: 主流程用 RankTakeout 口径) |
| `CountTakeoutPayment` | `statistics_takeout.go:299-392` | ❌ | 外卖支付方式 |
| `CountTakeoutReceivedAmount` | `statistics_takeout.go:393-450` | ⚠️ 同 CountTakeoutSale | (口径相同, 不单独 mirror) |
| **`RankTakeoutProduct`** ⭐ | `statistics_takeout.go:451-513` | ✅ **完全 mirror** | `takeout_line.py` / `sale_event.py` (核心外卖商品级算法) |
| `CountTakeoutBusinessTimePeriod` | `statistics_takeout.go:514-566` | ❌ | 外卖时段分析 |
| `CountTakeoutBusinessSummary` | `statistics_takeout.go:567-605` | ❌ | 外卖摘要 |
| `CountTakeoutChannelSale` | `statistics_takeout.go:606-644` | ❌ | 外卖渠道销售 (我们 P&L Sheet 4 部分覆盖) |
| `CountTakeoutChannelSaleByPlatform` | `statistics_takeout.go:645-684` | ❌ | **拆 Grab/LM/Shopee 各占多少**; 客户问平台分布时必需 |
| `CountTakeoutPaymentMethodRawData` | `statistics_takeout.go:685-769` | ❌ | 外卖支付方式 |
| `CountTakeoutCategory` | `statistics_takeout.go:770-867` | ❌ | 外卖品类 |
| `CountTakeoutProduct` | `statistics_takeout.go:868-978` | ❌ | 外卖商品销售 |
| `CountTakeoutRefundAmount` | `statistics_takeout.go:979-end` | ❌ | 外卖退款额; 我们外卖侧 refund 一律 0 (ttpos 没采集) |

---

### 0.3 合并算法 `service/statistics_util.go`

| ttpos 接口 | Go 源码:行 | mirror? | 我们实现 |
|---|---|:---:|---|
| `MergeTakeoutStatistics` | `statistics_util.go:32-150` | ⚠️ 仅对账锚 | `TTPOS_NET_SALES_SQL` 内置合并 (`shop + takeout amount`) |

---

### 0.4 统计 mirror 率

```
ttpos 后端 37 个统计函数
  ✅ 完全 mirror:        2 个 (5%)   CountProductSale / RankTakeoutProduct
  ⚠️ 部分 mirror:       3 个 (8%)   CountSale / CountTakeoutSale / MergeTakeoutStatistics
  ❌ Gap (未 mirror):  32 个 (87%)

总 mirror 率: ~13%
```

**为什么 mirror 率低**：

1. **大量函数是辅助接口** (日级 / 会员 / 自助餐税) — 我们月度集团 P&L 不需要
2. **税务 (CountTax / CountBuffetTax)** — 待财务对接，目前华莱士 tax=0
3. **会员系列 (6 个)** — ttpos 不采顾客详细信息, 我们 data-menu 已声明 ❌
4. **支付方式细分** — 月度报表不展开, 财务对账时再补
5. **Daypart / 时段** — 餐饮业界标准但我们暂未做

**YAGNI 原则**：mirror 率不是越高越好，**客户/业务真用到才补**。

### 0.5 真正应该 mirror 但还没做的 (gap top 5)

按业务价值 + 客户提问频率排序：

| Gap | 业务价值 | 何时补 |
|---|---|---|
| `CountTakeoutChannelSaleByPlatform` | 拆 Grab/LM/Shopee 各自营收占比 — 客户高频问 | 立刻补 (1 天) |
| `CountBusinessTimePeriod` + `CountTakeoutBusinessTimePeriod` | Daypart 时段分析 (早午晚) | 餐饮老板常问, 加 entity 列时段 |
| `CountTax` | 法定财报必需 (税务对账) | 财务对接时 (P0 文档里已标) |
| `CountRefundSummary` | 退款摘要 (异常分析) | 数据健康监测扩展 |
| `CountCategory` + `CountTakeoutCategory` | 按品类分析 | data-menu 已标 ⚠️, 高优先级 |

走 `/onboard-fact-table` skill 流程实施，每个 1 天工作量。

---


## 1. ✅ 完全 mirror 的算法

### 1.1 `CountProductSale` / `ExportProductSales` (堂食实收金额)

**ttpos 业务**：后台"销售统计 / 导出"接口的"实收金额"列。这是**堂食侧最权威口径**。

**ttpos Go 源码**：

```
ttpos-server-go/main/app/repository/statistics.go:1980-2046
  func ExportProductSales(...) {
      // actual_sale_amount = SUM(IF(free|give, 0, final_price * (num - refund_num)))
      // 时间字段: buildCountOpts 默认走 complete_time
      // 不能跟 RankProduct 混 —— 那个用 refund_time=0 过滤掉所有退款单
  }
```

**SQL 算法 (ttpos 等价)**：

```sql
SELECT SUM(
  IF(sp.free_num > 0 OR sp.give_num > 0, 0,
     sp.product_final_price * (sp.product_num - sp.refund_num))
) AS actual_amount
FROM ttpos_statistics_product sp
WHERE sp.complete_time >= {start} AND sp.complete_time < {end}
```

**我们的 mirror**：`semantic/entities/sale_line.py:42-43`

```sql
SUM(IF(sp.free_num > 0 OR sp.give_num > 0, 0,
       sp.product_final_price * (sp.product_num - sp.refund_num))
) AS actual_amount
```

**对账锚**：✅ TtposAnchorCheck 实测差 0.0002% (3464 万 / 69 元)

**差异说明**：无差异。**byte-equal**。

---

### 1.2 `RankTakeoutProduct` (外卖商品销售排行)

**ttpos 业务**：后台"外卖商品销售排行" top10 列表，是商品维度的销售统计。**注意**：跟外卖主统计 `CountTakeoutSale` **口径不同**。

**ttpos Go 源码**：

```
ttpos-server-go/main/app/repository/statistics_takeout.go:451-502
  func RankTakeoutProduct(...) {
      // 销量 = SUM(toi.quantity)
      // 销售额 = SUM(IF(t.order_state IN (10,20,30,40), toi.price * toi.quantity, 0))
      // 营业额状态 (10/20/30/40) 不含取消 60
      // 时间字段: dynamic — state=40 用 completed_time, 其它用 accepted_time
  }
```

**SQL 算法 (ttpos 等价)**：

```sql
SELECT
  toi.ttpos_product_package_uuid AS item_uuid,
  SUM(toi.quantity) AS qty,
  SUM(IF(t.order_state IN (10,20,30,40),
         toi.price * toi.quantity, 0)) AS sale_amount
FROM ttpos_takeout_order_item toi
JOIN ttpos_takeout_order t
  ON t.uuid = toi.takeout_order_uuid AND t.delete_time = 0
WHERE toi.delete_time = 0
  AND toi.ttpos_product_package_uuid > 0
  AND t.order_state IN (10, 20, 30, 40, 60)
  AND t.accepted_time > 0
  AND ((t.order_state = 40 AND t.completed_time >= {start} AND t.completed_time < {end})
    OR (t.order_state != 40 AND t.accepted_time >= {start} AND t.accepted_time < {end}))
GROUP BY item_uuid
```

**我们的 mirror**：`semantic/entities/takeout_line.py:22-56` (注释里明确写 "source: statistics_takeout.go:451-502")

**差异说明**：无差异。**byte-equal**。

**重点**：这是我们外卖 `actual_amount` 的算法来源。**不是** ttpos 主统计 `CountTakeoutSale` (那个用 `platform_total`)，两者口径不同 — 见第 2 节。

---

## 2. ⚠️ 部分 mirror 的算法 (口径差异)

### 2.1 `CountTakeoutSale` (外卖订单级实收)

**ttpos 业务**：后台"外卖营业额/实收金额"页面，是**外卖侧最权威口径**。

**ttpos Go 源码**：

```
ttpos-server-go/main/app/repository/statistics_takeout.go:200-286
  func CountTakeoutSale(...) {
      // total_sale_amount = SUM(IF(state IN valid, t.platform_total, 0))
      // total_pay_amount  = SUM(IF(state IN business, t.platform_total, 0))
      // total_business_amount = SUM(IF(state IN business, t.platform_total - t.tax, 0))
      // 用订单级 platform_total = subtotal + merchant_charge_fee - merchant_discount
  }
```

**SQL 算法 (ttpos 等价)**：

```sql
SELECT
  SUM(IF(t.order_state IN (10,20,30,40), t.platform_total, 0)) AS total_pay_amount
FROM ttpos_takeout_order t
WHERE t.delete_time = 0
  AND t.accepted_time > 0
  AND ((t.order_state = 40 AND t.completed_time IN [start, end))
    OR (t.order_state != 40 AND t.accepted_time IN [start, end)))
```

**我们的 mirror**：**仅在对账锚 mirror，主流程不用**。

- 对账锚：`semantic/reconciliation/checks/ttpos_anchor.py::TTPOS_NET_SALES_SQL` 跑这个算法验证
- 主流程：外卖 actual_amount 用 `RankTakeoutProduct` 算法 (§1.2)，不是 `CountTakeoutSale`

**差异**：

| 项 | `CountTakeoutSale` | 我们 (`takeout_line.py`) |
|---|---|---|
| SUM 字段 | 订单级 `platform_total` | item 级 `toi.price * toi.quantity` |
| 含 merchant_charge_fee | ✅ | ❌ |
| 含 merchant_discount | ✅ (减) | ❌ |
| 含 tax | ✅ | ❌ |

**华莱士现状下两者数值一致** (merchant_charge_fee = merchant_discount = 0, tax = 0)。
**对账锚实测差 0.0002%**。

**风险**：业务一旦启用商家服务费/商家折扣，我们的数字立刻偏离 ttpos 后台。需要决定要么：
- A. 改 `takeout_line.py` 改成 mirror `CountTakeoutSale` (用 platform_total)
- B. 保持现状，明确我们的"外卖营业额"定义跟 ttpos 后台不同

详见 `docs/profit-report-takeout-semantics.md` §3。

---

### 2.2 `CountSale` (总销售统计 = 堂食 + 外卖合并)

**ttpos 业务**：后台首页 / 销售总览，把堂食 + 外卖合并成一个总数。

**ttpos Go 源码**：

```
ttpos-server-go/main/app/service/statistics.go:141-240
  func CountSale(req CountReq) (*SaleData, error) {
      saleData = repository.NewStatisticsRepo(db).CountSale(...)         // 堂食侧
      takeoutData = repository.NewStatisticsTakeoutRepo(db).CountTakeoutSale(...) // 外卖侧
      MergeTakeoutStatistics(saleData, takeoutData, ...)  // 合并
      // 返回:
      //   TotalReceivedAmount = 堂食 actual + 外卖 TotalPayAmount  (= 实收金额)
      //   TotalBusinessAmount = 堂食 + 外卖业务金额                (= 营业收入, 扣 tax)
      //   TotalTakeoutSaleAmount  独立列出, 不合并                  (= 外卖销售额)
      //   TotalTakeoutBusinessAmount 独立列出, 不合并                (= 外卖营业收入)
  }
```

**我们的 mirror**：

- `semantic/entities/total_line.py::merged_cte` ≈ `MergeTakeoutStatistics` 合并部分
- 但**只合并 actual_amount**，没合并 `TotalBusinessAmount` (没扣 tax)
- ttpos 单独列的 `TotalTakeoutSaleAmount` / `TotalTakeoutBusinessAmount` 我们 P&L 里用 Sheet 4 按渠道对比展示

**差异**：

| 项 | ttpos `CountSale` | 我们 |
|---|---|---|
| 总实收 | 合并 | ✅ Net Sales |
| 总营业额扣 tax | ✅ TotalBusinessAmount | ❌ 我们不扣 tax (华莱士 tax=0) |
| 外卖单独字段 | ✅ TotalTakeoutSaleAmount | ⚠️ 走 channel='takeout' SUM |

---

### 2.3 `MergeTakeoutStatistics` (合并工具)

**ttpos 业务**：内部工具函数，把堂食 SaleData + 外卖 TakeoutData 合并到一份。

**ttpos Go 源码**：`ttpos-server-go/main/app/service/statistics_util.go:32-150`

```go
// 关键: TotalReceivedAmount = sale.TotalReceivedAmount + takeout.TotalPayAmount
//       (takeout 用的是 TotalPayAmount, 不是 TotalSaleAmount)
```

**我们的 mirror**：`semantic/reconciliation/checks/ttpos_anchor.py::TTPOS_NET_SALES_SQL`
内置合并 (`shop_sale.amount + takeout_sale.amount`)。

**差异**：算法层面一致；接口层面我们没暴露"分别拿堂食 vs 外卖"两个数 (P&L Sheet 4 替代)。

---

## 3. ❌ 没 mirror 的接口 (gap 清单)

下面这些 ttpos 接口**我们没消费**，未来要补 mirror 的时机：

| ttpos 接口 | Go 源码 | 何时要补 |
|---|---|---|
| `CountTakeoutPayment` | `statistics_takeout.go:299-386` | 客户问"外卖各支付方式占比" |
| `CountTakeoutBusinessTimePeriod` | `statistics_takeout.go:514-557` | 做 Daypart 时段分析 |
| `CountTakeoutChannelSale` | `statistics_takeout.go:606-639` | 拆 Grab / LINE MAN / Shopee 各渠道营收 |
| `RankProduct` (堂食 top10) | `statistics.go:1245+` | 客户要堂食商品排行 |
| `CountBusiness` (业务总览) | `service/business.go:432-608` | 做"每店日报"功能 |
| `CountBusinessSummary` | `service/statistics.go:2140` | 做摘要报表 |
| `CountChannelSales` (跨渠道) | `service/business.go:3095` | 跨渠道对比报表 (堂食 vs Grab vs LM) |
| `CountTakeoutReceivedAmount` | `statistics_takeout.go:393-444` | 跟 CountTakeoutSale 同算法，没必要单独 mirror |

**何时不补**：客户/老板没问到、报表没要、对账没需要 → 不要为了"全 mirror"而 mirror，YAGNI。

---

## 4. ttpos 内部口径漂移 (关键风险点)

ttpos 自己**内部 6 个外卖统计接口口径不一致**。这是个需要长期关注的风险：

| 类别 | 接口 | 算法字段 |
|---|---|---|
| **主统计 (5 个)** | CountTakeoutSale / ReceivedAmount / Payment / BusinessTimePeriod / ChannelSale | 订单级 `platform_total` |
| **商品排行 (1 个)** | RankTakeoutProduct | item 级 `toi.price * toi.quantity` |

**我们抄的是 RankTakeoutProduct (商品排行)**，不是主统计。

**风险**：
- 当前数值一致 (merchant_charge_fee = merchant_discount = 0)
- 业务启用商家服务费后**立刻偏离**
- 客户对账时会发现"我们外卖营业额跟 ttpos 主页面对不上"

**缓解**：TtposAnchorCheck 自动报警 (差 > 0.001% 就 MUST_FIX)。

详见 `docs/profit-report-takeout-semantics.md` §3.1。

---

## 5. 升级响应规则

ttpos 后端升级时本仓库怎么响应：

### 5.1 ttpos 改字段名 (e.g. `product_sale_price` → `unit_sale_price`)

**响应**：
1. 改 `semantic/entities/sale_line.py:38` SQL 字段名
2. 跑 `tests/test_resolver_parity.py` + 全套 → 验证零行为变化
3. 调 `/sync-docs` skill → 同步 `data-contract.md::§3.1` 字段清单 + 本文档 §1.1 公式

### 5.2 ttpos 改算法 (e.g. `CountProductSale` actual_amount 改成不扣 refund)

**响应**：
1. **先评估**：要不要跟着改？(财务对账要求 + 业务一致性)
2. 改 → 改 `sale_line.py:42-43` 公式
3. 跑 `TtposAnchorCheck` 验证差额仍 < 0.001%
4. 同步本文档 §1.1 + `metrics-catalog.md::Net Sales`

### 5.3 ttpos 加新统计接口 (e.g. `CountStoreLevelMarketing`)

**响应**：
1. 评估 → 客户/业务需要吗？
2. 需要 → 走 `/onboard-fact-table` skill 流程接入
3. 同步本文档 §3 (gap 清单往 mirror 转)

### 5.4 ttpos 废弃算法 (e.g. 删除 `CountTakeoutBusinessTimePeriod`)

**响应**：
1. 评估 → 我们消费了吗？
2. 没消费 → 同步本文档 §3 删除该 entry
3. 消费了 → 评估替代 / 内部实现迁移

---

## 6. 对账锚状态

跟 ttpos 后端算法对账的现状：

| 算法 | 对账锚 | 状态 | 实测差额 |
|---|---|---|---|
| 堂食 actual_amount (`CountProductSale`) | TtposAnchorCheck (含堂食) | ✅ | 部分体现在 0.0002% 总差 |
| 外卖 actual_amount (`CountTakeoutSale`) | TtposAnchorCheck (含外卖) | ✅ | 同上 |
| 外卖 RankTakeoutProduct vs CountTakeoutSale | 内部口径差 | ⚠️ 监控 | 华莱士现状 = 0 |
| `MergeTakeoutStatistics` 合并 | TtposAnchorCheck 总和 | ✅ | 69 元 (2026-04) |
| 其它接口 (gap 里的) | — | — | 没消费就不对账 |

---

## 7. 维护规则

**改 `semantic/entities/*.py` 时**：

1. 看看改的算法是否在本文档 §1 / §2 列出
2. 如果是，**必须同步改本文档**对应公式
3. 跑 `TtposAnchorCheck` 重新验证差额
4. commit 信息附 "mirror updated"

**走 `/sync-docs` skill**：

skill 流程里会自动检查 `entities/*.py` 改动 → 提示本文档要不要更新。

---

## 8. 跟其他文档的关系

```
ttpos-algorithms-mirror.md (本文档)
  ↓ 算法层面 mirror

data-contract.md (字段使用清单)
  ↓ 字段层面契约

ttpos-bq-field-pitfalls.md (字段陷阱)
  ↓ 字段使用注意事项

metrics-catalog.md (我们的指标口径)
  ↓ 业务层面定义

data-menu.md (业务菜单)
  ↓ 给非工程师看
```

四份从底到顶覆盖完整：算法 → 字段 → 指标 → 业务。

## 9. 一句话总结

> ttpos 是上游源系统，它的算法是**ground truth**。我们做的事是：
> - **完全 mirror** 2 个核心算法 (CountProductSale + RankTakeoutProduct)
> - **部分 mirror** 1 个 (CountTakeoutSale 仅对账锚用)
> - **8 个 gap** 留作未来扩展候选
>
> ttpos 升级时本仓库**用本文档为 contract** 跟着同步，不靠记忆 / 不靠考古。
>
> 客户问"为什么我们跟 ttpos 一致" — TtposAnchorCheck 0.0002% 是硬证明。
> 客户问"为什么不一致" — 翻 §2 看口径差异。

## 相关文档

- [data-contract.md](./data-contract.md) — 字段使用契约 (本文档之上)
- [ttpos-bq-field-pitfalls.md](./ttpos-bq-field-pitfalls.md) — 字段陷阱
- [profit-report-takeout-semantics.md](./profit-report-takeout-semantics.md) — 外卖口径调研 (本文档 §2.1 / §4 详细背景)
- [metrics-catalog.md](./metrics-catalog.md) — 我们的语义层指标定义
- `ttpos-server-go/main/app/repository/statistics_takeout.go` — ttpos 后端外卖统计
- `ttpos-server-go/main/app/repository/statistics.go` — ttpos 后端堂食统计
- `ttpos-server-go/main/app/service/statistics_util.go` — ttpos 后端合并工具
