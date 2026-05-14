# ttpos BigQuery 字段陷阱清单

> 这次方案落地踩过的所有 ttpos BQ 字段坑。下次有人（包括你 / AI 助手）做同样
> 调研，先翻这里**至少省半天**。
>
> 跟 `.claude/skills/bigquery-export/schema-reference.md` 互补：那个是表结构
> 速查（哪些字段），本文是字段陷阱（哪些字段会让你出错）。
>
> 归档日期 2026-05-13。

## 1. Timestamp / 时间字段

### 1.1 字段是 UNIX 秒（不是毫秒）

`accepted_time` / `completed_time` / `complete_time` 都是 **UNIX 秒**。

```python
# 正确
TIMESTAMP_SECONDS(completed_time)   # → 2026-05-13 02:54:53 UTC

# 错误 (会得到 1970-01-21 的鬼数据)
TIMESTAMP_MILLIS(completed_time)
```

### 1.2 算 epoch 时**注意年份偏差** — 我踩过

```python
# 真实踩坑实例:
# 我以为 1740758400 是 2026-03-01 BKK, 实际是 2025-03-01
# 浪费一轮 BQ 查询全部 0 数据才发现

# 正确算 epoch:
from datetime import datetime, timezone, timedelta
BKK = timezone(timedelta(hours=7))
s = datetime(2026, 4, 1, tzinfo=BKK).timestamp()  # 1774976400
e = datetime(2026, 5, 1, tzinfo=BKK).timestamp()  # 1777568400
```

**永远用 Python 算 epoch，不要凭印象算**。

### 1.3 Dynamic time condition (外卖最大坑)

ttpos 后端 `RankTakeoutProduct` / `CountTakeoutSale` 的时间过滤逻辑：

```sql
-- state=40 (已完成) 用 completed_time
-- state != 40 (10/20/30/60) 用 accepted_time
WHERE accepted_time > 0
  AND (
    (order_state = 40 AND completed_time >= {start} AND completed_time < {end})
    OR
    (order_state != 40 AND accepted_time >= {start} AND accepted_time < {end})
  )
```

**统一用一个时间字段会少 10-30% 数据**（取消订单 accepted_time 在期间内但 completed_time 0）。

源参考：`ttpos-server-go/main/app/repository/statistics_takeout.go:451-502`。

### 1.4 软删字段约定

ttpos 用 `delete_time = 0` 表示"未删除"，**不是用 NULL**。所有 SQL 必须 `WHERE delete_time = 0` 过滤。

## 2. 订单状态码 (order_state)

### 2.1 外卖 state code

| state | 含义 | 算营业额？ |
|---|---|---|
| 10 | 已下单 (新订单) | ✅ |
| 20 | 已接单 (商家确认) | ✅ |
| 30 | 制作中 | ✅ |
| 40 | 已完成 (已送达) | ✅ |
| 60 | 已取消 | ❌ (但仍要单独统计 cancelled_qty) |

**常见错误**：只统计 state=40 会漏掉 10-30% 的有效营业额（10/20/30 还在过程中但已成交）。

### 2.2 堂食没有 state 概念

`ttpos_statistics_product` 直接就是已成交记录，没 order_state 字段。`free_num`/`give_num`/`refund_num` 才是堂食侧的"特殊状态"。

## 3. 字段语义陷阱

### 3.1 `toi.price` vs `toi.ttpos_price` (外卖侧最大混淆)

| 字段 | 含义 | converter 行为 |
|---|---|---|
| `toi.price` | 外卖平台显示的实付价 | 平台 payload `item.GetPrice() / 100` 直接来 |
| `toi.ttpos_price` | POS 系统里这个商品的标价快照 | converter 阶段为空，service 层后填 |

**关键**：
- `toi.price` 永远有值（外卖订单必有）
- `toi.ttpos_price` 历史数据覆盖率 **99.6%**（早期数据可能为 0）
- 两者不等时说明该订单走了平台促销 / 商家差异化定价

### 3.2 `merchant_charge_fee` 不是平台抽佣 (我前面误判过)

```
platform_total = subtotal + merchant_charge_fee - merchant_discount
                            ^^^^^^^^^^^^^^^^^^^
                            这是商家自收的小单费/打包费, +号项
                            **不是平台从商家抽走的钱**
```

**ttpos 完全没有"平台抽佣"字段**。要拿真实抽佣只能外接平台对账单。

### 3.3 `subtotal` 计算行为不对称 (Grab vs LINE MAN)

跟 ttpos converter 源码对：

- **Grab**: `subtotal = price.GetSubtotal()` 直接信平台 payload
- **LINE MAN**: `subtotal` 在赋值后又被**覆盖重算** `Σ(item.total_price)` (lineman_order_converter.go:343-346)

**意味着** Grab `subtotal` ≠ Σ(toi.price × qty) 是可能的，LINE MAN 一定相等。

### 3.4 `eater_payment` 在 Grab 非现金支付时被覆盖

`grab_order_converter.go:219-227`：
- 现金支付 / 配送方=餐厅：`eater_payment` = 平台原值
- 其它：`eater_payment = subtotal` 覆盖，**原值丢了**

**用 eater_payment 时记得这个陷阱**——可能不是真实顾客实付。

### 3.5 `delete_time` 不能误删过滤

`pp.delete_time != 0` 的商品**仍在 ttpos_product_package 里**，`pp.price` 是删除前最后值。聚合时如果直接 JOIN 不过滤，会拉到删除商品的数据。

**正确做法**：`LEFT JOIN ... ON pp.uuid = se.item_uuid` 不过滤 delete_time（让删除商品的销量也算上），但 `pp.price` 字段当作"参考值"用，不要做计算锚。

## 4. Schema / 数据集陷阱

### 4.1 `shop{uuid_str}` 命名规则

每家店一个独立的 BQ dataset，命名：`shop{uuid}`（uuid 是 19 位整数，不带前缀）。

```python
dataset = f"shop{uuid_str}"  # 例: shop2598648160256000
```

### 4.2 30+ 家店没 `ttpos_takeout_order` 表

这次扫数据发现：**194 个候选 dataset 里只有 45 家店有外卖订单表**。其它店要么没外卖业务、要么 ttpos 没初始化外卖模块。

**意味着**：
- 跑 BQ 时如果 hard-fail 在 NotFound 错误，全集团报表会挂
- 必须用 try/except 或先用 `INFORMATION_SCHEMA.TABLES` 探测

### 4.3 多个 dataset 的 schema 不完全一致

不同店初始化时间不同，新加字段在老 dataset 可能没有。SQL 写 `SELECT * FROM ... WHERE col IS NOT NULL` 类型的依赖可能在某些店挂。

**建议**：显式 SELECT 字段，不要 `SELECT *`。

### 4.4 BQ region 是 `asia-southeast1`

不是默认的 US。`client = bigquery.Client(project="diyl-407103", location="asia-southeast1")`，否则跨 region 查询慢且收费。

## 5. ttpos 内部多口径并存

### 5.1 外卖统计 6 个函数算法不同

`ttpos-server-go/main/app/repository/statistics_takeout.go` 有 6 个函数，**口径不一致**：

| 函数 | 计算字段 | 用途 |
|---|---|---|
| `CountTakeoutSale` (主) | `platform_total` (订单级) | 后台"实收/营业额" 页 |
| `CountTakeoutReceivedAmount` | 同上 | 收银日报 |
| `CountTakeoutPayment` | 同上 | 支付汇总 |
| `CountTakeoutBusinessTimePeriod` | 同上 | 时段分析 |
| `CountTakeoutChannelSale` | 同上 | 渠道分析 |
| `RankTakeoutProduct` (排行) | `toi.price * toi.quantity` (item 级) | 商品销售排行 |

**注意**：我们 `semantic/entities/takeout_line.py` 抄的是 `RankTakeoutProduct`，不是主统计 `CountTakeoutSale`。在华莱士业务下（merchant_charge_fee = merchant_discount = 0）两者数值一致；但**业务一旦启用商家服务费/商家折扣，立刻偏离**。

详见 `docs/profit-report-takeout-semantics.md`。

### 5.2 堂食 ExportProductSales vs RankProduct

`ttpos-server-go/main/app/repository/statistics.go` 也有两个堂食统计：

- `ExportProductSales` (statistics.go:1980-2046) — 导出接口算法
- `RankProduct` (statistics.go:1245) — top10 排行算法

**差异**：RankProduct 用 `refund_time=0` 过滤掉所有退款单（哪怕没全退），ExportProductSales 用 `(num - refund_num)` 扣退款。

我们 `sale_line.py` 抄的是 **ExportProductSales 算法**（更精确）。

## 6. ttpos 完全缺失的关键数据

下面这些**ttpos 数据库里根本没有**，要做完整经营分析必须外接：

| 缺失项 | 影响 | 外接来源 |
|---|---|---|
| 平台抽佣 (Grab/LINE MAN/Shopee commission) | 外卖真实利润算不准 | 平台月度对账单 Excel |
| 配送费分担 | 外卖成本算不准 | 平台月度对账单 |
| 支付通道手续费 (Robinhood/银行)| 实收金额跟银行流水对不上 | 银行流水 / 支付通道账单 |
| 房租 / 人力 / 水电 / 营销 | Operating Income 算不出 | 财务 ERP / HR 系统 |
| 法定财报口径 (含税/科目代码)| 法定报表对不上 | 财务 ERP |
| 顾客 ID / 复购数据 | 客户分析做不了 | ttpos 不采集顾客信息 |

详见 `docs/pnl-accounting-standards-gap.md`。

## 7. ttpos_product_package (商品主表) 陷阱

### 7.1 `pp.name` 是 JSON 多语言

```sql
JSON_EXTRACT_SCALAR(pp.name, '$.zh')   -- 中文名
JSON_EXTRACT_SCALAR(pp.name, '$.en')   -- 英文名
JSON_EXTRACT_SCALAR(pp.name, '$.th')   -- 泰文名
```

不是普通字符串。直接 `SELECT pp.name` 拿到的是 JSON 字符串。

### 7.2 商品名末尾带不可见字符

`pp.name` 经常带 `_x000D_` 等隐式空白 / 换行 / 制表符。**必须 REGEXP_REPLACE 清洗**：

```sql
REGEXP_REPLACE(COALESCE(
  JSON_EXTRACT_SCALAR(pp.name, '$.zh'),
  JSON_EXTRACT_SCALAR(pp.name, '$.en'),
  '未知'
), r'^\s+|\s+$', '') AS item_name
```

不清洗的后果：同一商品在 Excel 看像两个不同 SKU。

### 7.3 `pp.product_type` 0/1 区分单品套餐

- `pp.product_type = 0` — 单品
- `pp.product_type = 1` — 套餐

**套餐 BOM** 不在 `ttpos_product_bom` 里，要去 `ttpos_product_package_group` + `ttpos_product_package_group_item` 拿子商品列表。

## 8. ttpos_product_bom (BOM 主表) 陷阱

### 8.1 多规格商品 BOM 行重复

同一商品多个 `product_bom` 记录（不同规格但共用 bom_card）→ 同一 material_code 出现 N 次。

**必须在加载时去重**（`_load_boms` 已经做了：`seen_keys = {(store, item, material)}`）。

### 8.2 BOM 单位 (`bom_unit`) 跟物料单位 (`unit_name`) 不一致

`product_bom.bom_num` 的单位是 `bom_unit`（克 / 个 / 份），物料管理里 `ttpos_material.unit_name` 可能是 kg / 公斤 / pcs。

ERPNext 价格 + base_unit_conversion_rate 用来换算到 BOM 单位。我们 Resolver 单价里有这个修正（`BOM_UNIT_CORRECTIONS`）。

### 8.3 `pb.delete_time != 0` 的 BOM 仍要用

软删 BOM 经常是因为商品下架但历史订单还在跑——直接过滤会让历史 SKU 找不到 BOM。

**`_load_boms` v5 实现**：当全店该商品没 active BOM 时，**fallback 到软删的 BOM**。

## 9. 排障速查

| 症状 | 看本文档哪一节 |
|---|---|
| BQ 查不到数据 | §1.2 epoch 算错 / §4.2 部分店没 takeout 表 |
| 外卖营业额对不上后台 | §5.1 6 个口径 / §3.2 merchant_charge_fee |
| Net Sales 跟实收对不上 | §3.4 eater_payment 覆盖逻辑 |
| BOM 数量翻倍 | §8.1 多规格重复, dedup 没做 |
| 单份成本异常 | §8.2 单位不一致 |
| 商品名两个 SKU | §7.2 不可见字符 |
| `delete_time = 0` 漏数据 | §1.4 软删字段约定 |

## 10. 给 AI 助手的提示

如果你（AI）在 BQ 上做 ttpos 数据调研，**遇到任何"数据为 0"/"数字不对"先回到本文档第 1 章**。我（人类作者）这次会话浪费过：
- 一轮在 epoch 算错 (§1.2)
- 一轮在 takeout 表不存在 (§4.2)
- 一轮在 merchant_charge_fee 误判 (§3.2)

每个坑都浪费 10-20 分钟。先看本文档能省半天。

## 相关文档

- [.claude/skills/bigquery-export/schema-reference.md](../.claude/skills/bigquery-export/schema-reference.md) — ttpos 表结构速查
- [docs/profit-report-takeout-semantics.md](./profit-report-takeout-semantics.md) — 外卖口径调研归档
- [docs/profit-margin-reconciliation-checklist.md](./profit-margin-reconciliation-checklist.md) — F vs G 对账方法论
- [docs/pnl-accounting-standards-gap.md](./pnl-accounting-standards-gap.md) — 缺失数据 / 财务对接
