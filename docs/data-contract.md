# 数据契约: 报表服务 vs ttpos

> 显性化我们消费 ttpos 的范围 + 转换规则，让**语义层跟 ttpos 物理 schema 解耦**。
> 报表服务作为独立 product 上线时，本文档就是跟 ttpos 团队的合约。
>
> 业界对应：
> - **Data Contract** (Chad Sanderson, data-mesh)
> - **Source-to-Target Mapping (STTM)** (ETL 经典)
> - **Anti-Corruption Layer** (DDD, Eric Evans)
> - **Bronze → Silver Schema Mapping** (Databricks Medallion)
>
> 归档日期 2026-05-13。

## 1. 全景图

```
┌──────────────────────────────────────────────┐
│  ttpos BigQuery (物理层, 不归我们控制)         │
│                                              │
│  9 张表 / ~50 个字段被我们消费               │
└──────────────┬───────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│  Anti-Corruption Layer (我们的契约边界)       │
│  semantic/entities/*.py — SQL CTE 工厂        │
│                                              │
│  把 ttpos 物理字段 → 我们的标准语义字段       │
└──────────────┬───────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│  Our Domain (语义层, 完全自主)                │
│                                              │
│  18 个标准语义字段, 不直接 reference ttpos     │
│  入口: SaleEvent / SaleLine / TakeoutLine /  │
│        TotalLine / Bom / Combo / PriceBreakdown│
└──────────────┬───────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│  Applications (报表 / KPI / 对账 / 分析)      │
│                                              │
│  profit_margin / profit_by_price /           │
│  pnl_statement / reconciliation / analytics  │
└──────────────────────────────────────────────┘
```

**关键洞察**：报表只跟我们的语义层耦合，**不直接访问 ttpos 字段**。ttpos 改字段时，
只需修 entities 里的 CTE 工厂，下游全部自动适配。

## 2. 我们消费的 ttpos 表清单

| ttpos 表 | 用途 | 引用位置 |
|---|---|---|
| `ttpos_statistics_product` | 堂食销售事实 | `sale_line.py` / `sale_event.py` / `price_breakdown.py` |
| `ttpos_takeout_order_item` | 外卖商品事实 | `takeout_line.py` / `sale_event.py` / `price_breakdown.py` |
| `ttpos_takeout_order` | 外卖订单元数据 (JOIN) | `takeout_line.py` / `sale_event.py` / `ttpos_anchor.py` |
| `ttpos_product_package` | 商品主表 (商品名/标价/类型) | 多处 JOIN |
| `ttpos_product_bom` | BOM 主表 | `bom.py` |
| `ttpos_related_material` | BOM 物料关联 | `bom.py` |
| `ttpos_material` | 物料主表 (编码/单价) | `bom.py` |
| `ttpos_product_package_group` | 套餐结构 | `combo.py` |
| `ttpos_product_package_group_item` | 套餐子商品 | `combo.py` |
| `ttpos_setting` | 店属性 (店编号/店名) | `profit_margin_report.py::_fetch_store_names_from_bq` |

共 **10 张表**。

## 3. 字段使用清单 (ttpos → 我们语义)

### 3.1 `ttpos_statistics_product` (堂食销售)

| ttpos 字段 | 类型 | 我们用作 | 我们语义字段 | 引用 |
|---|---|---|---|---|
| `product_package_uuid` | NUMERIC(20) | 商品标识 | `item_uuid` | `sale_line.py:35` / `sale_event.py:26` |
| `product_sale_price` | NUMERIC(20,4) | 堂食标价 (订单录入价) | 堂食 `price` / `sales_price` 因子 | `sale_line.py:38` / `sale_event.py:27` |
| `product_final_price` | NUMERIC(20,4) | 堂食成交价 (折后) | `actual_amount` 因子 | `sale_line.py:43` |
| `product_num` | INT64 | 销量 (含赠/退/折扣件) | `qty` | 多处 |
| `free_num` | INT64 | 赠品数量 | `free_qty` | `sale_line.py:46` |
| `give_num` | INT64 | 赠送数量 | `give_qty` | `sale_line.py:47` |
| `refund_num` | INT64 | 退款数量 | `refund_qty` | `sale_line.py:44` |
| `complete_time` | INT64 (UNIX 秒) | 订单完成时间 | 时间过滤 | `sale_line.py:58` 等 |
| `member_order_discount_rate` | NUMERIC | 会员订单折扣率 | `avg_member_discount` | `sale_line.py:47` |

**没用的字段（但 ttpos 里有）**：`order_uuid` / `bill_uuid` / `staff_uuid` / 各种 audit 字段等。

### 3.2 `ttpos_takeout_order_item` (外卖商品)

| ttpos 字段 | 类型 | 我们用作 | 我们语义字段 | 引用 |
|---|---|---|---|---|
| `ttpos_product_package_uuid` | NUMERIC(20) | 外卖商品标识 | `item_uuid` | `takeout_line.py:23` / `sale_event.py:60` |
| `price` | NUMERIC(20,4) | 平台实付价 (含可能促销) | 外卖 `price` / `sales_price` | `takeout_line.py:28` |
| `quantity` | INT64 | 销量 | `qty` | `takeout_line.py:25` |
| `takeout_order_uuid` | NUMERIC(20) | JOIN key | (内部 JOIN 用) | `takeout_line.py:45` |
| `delete_time` | INT64 | 软删过滤 | `WHERE delete_time = 0` | 多处 |

**没用的字段**：`ttpos_price` (POS 标价快照, 跟 `price` 不同但我们目前没用) / `tax` / `specifications` / `platform_item_id` / converter 字段。

⚠️ **重点**：`ttpos_price` 我们**没消费**——它是 POS 标价跟 `price` (平台实付) 不同。
未来要做"外卖价 vs 标价折让分析"需要补这个字段。详见
[ttpos-bq-field-pitfalls.md](./ttpos-bq-field-pitfalls.md) §3.1。

### 3.3 `ttpos_takeout_order` (外卖订单元数据)

| ttpos 字段 | 类型 | 我们用作 | 我们语义字段 | 引用 |
|---|---|---|---|---|
| `uuid` | NUMERIC(20) | JOIN key | — | 多处 |
| `order_state` | INT64 | 订单状态 | 时间窗 + cancelled 判定 | `takeout_line.py:50` / `sale_event.py:84` |
| `accepted_time` | INT64 (UNIX 秒) | 接单时间 (state != 40) | 时间过滤 | `takeout_line.py:51-55` |
| `completed_time` | INT64 (UNIX 秒) | 完成时间 (state = 40) | 时间过滤 | 同上 |
| `delete_time` | INT64 | 软删 | `WHERE delete_time = 0` | 多处 |
| `platform_total` | NUMERIC(20,4) | 平台结算总额 | **仅** `ttpos_anchor.py` 对账锚用 | `ttpos_anchor.py::TTPOS_NET_SALES_SQL` |

**没用的字段** (但 ttpos 里有, 业务上有价值)：
- `subtotal` — 商品小计
- `merchant_charge_fee` — 商家自收小单费 (华莱士 = 0)
- `merchant_discount` — 商家承担优惠 (华莱士 = 0)
- `platform_discount` — 平台贴钱优惠
- `delivery_fee` — 配送费
- `eater_payment` — 顾客实付
- `tax` — 税费
- `basket_promo` — 购物车优惠
- `platform` — Grab / LINE MAN / Shopee 标识 ⚠️ 跟 PlatformPayoutCheck 强相关, 但目前没消费

### 3.4 `ttpos_product_package` (商品主表)

| ttpos 字段 | 类型 | 我们用作 | 我们语义字段 | 引用 |
|---|---|---|---|---|
| `uuid` | NUMERIC(20) | 商品 ID | `item_uuid` (JOIN) | 多处 |
| `name` | STRING (JSON) | 多语言商品名 | `item_name` (清洗后) | `profit_margin_report.py:531-535` |
| `price` | NUMERIC(20,4) | POS 后台当前牌价 | `list_price` (审计参考列, 不参与计算) | `profit_margin_report.py:560` |
| `product_type` | INT | 单品/套餐区分 (0/1) | `is_combo` 标志 | `pnl_statement.py:_PNL_SALES_SQL` |
| `category_uuid` | NUMERIC(20) | 商品分类 | ⚠️ 暂未消费 (data-menu 标 ⚠️) | — |
| `delete_time` | INT64 | 软删标记 | **不过滤** (用 `pp.price` 当参考列) | — |

### 3.5 `ttpos_product_bom` (BOM 主表)

| ttpos 字段 | 类型 | 我们用作 | 引用 |
|---|---|---|---|
| `uuid` | NUMERIC(20) | JOIN key | `bom.py:25` |
| `product_package_uuid` | NUMERIC(20) | 关联到商品 | `bom.py:34` |
| `product_bom_card_uuid` | NUMERIC(20) | BOM 卡片关联 | `bom.py:47-50` |
| `delete_time` | INT64 | 软删 | `bom.py:55` (含 fallback 逻辑) |

### 3.6 `ttpos_related_material` (BOM 物料关联)

| ttpos 字段 | 类型 | 我们用作 | 引用 |
|---|---|---|---|
| `related_uuid` | NUMERIC(20) | JOIN key | `bom.py:49-50` |
| `material_uuid` | NUMERIC(20) | 关联到物料 | `bom.py:54` |
| `num` | NUMERIC | BOM 消耗数量 | `bom_num` |
| `unit_name` | STRING (JSON) | 单位多语言 | `bom_unit` (多语言 fallback 链) |
| `base_unit_name` | STRING (JSON) | 基础单位 | 同上 fallback |
| `base_unit_conversion_rate` | NUMERIC | 单位换算系数 | `conversion_rate` |
| `delete_time` | INT64 | 软删 | `bom.py:52` |

### 3.7 `ttpos_material` (物料主表)

| ttpos 字段 | 类型 | 我们用作 | 引用 |
|---|---|---|---|
| `uuid` | NUMERIC(20) | JOIN key | `bom.py:54` |
| `code` | STRING | 物料编码 | `material_code` |
| `name` | STRING (JSON) | 物料名 | `material_name` |
| `price` | NUMERIC | BQ 内置物料单价 | `material_bq_price` (Resolver priority=0 兜底) |
| `delete_time` | INT64 | 软删 | `bom.py:54` |

### 3.8 `ttpos_product_package_group` + `_group_item` (套餐结构)

参考 `semantic/entities/combo.py`。字段：
- group_uuid / product_package_uuid / child_uuid / child_num / weight

### 3.9 `ttpos_setting` (店属性)

`key = 'store'` 的 row, `values` JSON 含 `store_code` / `store_name`。

## 4. 我们的语义字段定义 (Our Domain)

跟下游 (报表 / KPI) 直接对接的**18 个标准字段**：

### 4.1 维度字段

| 语义字段 | 类型 | 业务含义 | 来源 |
|---|---|---|---|
| `item_uuid` | string | 商品唯一标识 | `product_package_uuid` (堂食) / `ttpos_product_package_uuid` (外卖) |
| `item_name` | string | 商品名 (中文段, 已清洗) | `JSON_EXTRACT_SCALAR(pp.name, '$.zh')` 清洗后 |
| `is_combo` | bool | 套餐 / 单品 | `pp.product_type == 1` |
| `price` | float | 实际成交价 | 堂食 `product_sale_price` / 外卖 `toi.price` |
| `channel` | enum | 销售渠道 | 硬编码 `'dine'` / `'takeout'` |
| `store_num` | string | 店编号 | `ttpos_setting.values.store_code` |
| `store_name` | string | 店名 | `ttpos_setting.values.store_name` |
| `period` | string | 期间标识 ("2026-04") | CLI 参数 |

### 4.2 度量字段 (跨 channel 同口径)

| 语义字段 | 类型 | 业务含义 | 转换规则 |
|---|---|---|---|
| `qty` | int | 销量 (含赠/退/取消件) | `SUM(product_num)` (堂食) / `SUM(quantity)` (外卖) |
| `sales_price` | float | 营业额 (实际成交价 × 销量) | `SUM(price × qty)` |
| `actual_amount` | float | 实收金额 (扣完损失项) | 堂食 `SUM(IF free|give, 0, final_price × (num-refund))`; 外卖 `SUM(price × qty)` active states |
| `refund_qty` | int | 退款件数 | 堂食 `SUM(refund_num)` / 外卖 = 0 |
| `refund_amount` | float | 退款金额 | 堂食 `SUM(sale_price × refund_num)` |
| `free_qty` / `give_qty` | int | 赠品/赠送件数 | 堂食 only |
| `free_amount` / `give_amount` | float | 赠品/赠送整行标价金额 | 堂食 only |
| `discount_amount` | float | 调价折扣 (标价−成交价) × 已售件数 | 堂食 only |
| `cancelled_qty` / `cancelled_amount` | int/float | 外卖取消订单 (state=60) | 外卖 only |

### 4.3 字段语义对账锚

`actual_amount` **等价于** ttpos 后端 `CountProductSale.actual_sale_amount`，
跟 ttpos 后台对账差 0.0002% (`TtposAnchorCheck` 验证)。

## 5. 转换规则 (Anti-Corruption Layer 核心)

### 5.1 时间过滤 (`dynamic time condition`)

跟 ttpos 后端 `RankTakeoutProduct` 一致：

```sql
-- state=40 (已完成) 用 completed_time
-- state != 40 (10/20/30/60) 用 accepted_time
WHERE accepted_time > 0
  AND (
    (order_state = 40 AND completed_time IN [start, end))
    OR
    (order_state != 40 AND accepted_time IN [start, end))
  )
```

实施位置：`takeout_line.py:51-55` / `sale_event.py` / `ttpos_anchor.py`。

### 5.2 软删过滤

| 表 | 我们怎么做 | 原因 |
|---|---|---|
| `ttpos_statistics_product` | 不过滤 (无 delete_time) | — |
| `ttpos_takeout_order` | `WHERE delete_time = 0` | 标准 |
| `ttpos_takeout_order_item` | `WHERE delete_time = 0` | 标准 |
| `ttpos_product_package` | **不过滤** | 删除商品仍有历史销量，pp.price 当参考列 |
| `ttpos_product_bom` | **条件性过滤** (`delete_time = 0 OR active_count = 0`) | 全店没 active BOM 时回退到软删的 |
| `ttpos_related_material` | `WHERE delete_time = 0` | 标准 |
| `ttpos_material` | `WHERE delete_time = 0` | 标准 |

### 5.3 多语言字段 (`name` 字段都是 JSON)

```sql
REGEXP_REPLACE(COALESCE(
  JSON_EXTRACT_SCALAR(pp.name, '$.zh'),
  JSON_EXTRACT_SCALAR(pp.name, '$.en'),
  '未知'
), r'^\s+|\s+$', '') AS item_name
```

**必须清洗不可见字符** (尾巴常带 `_x000D_` 等)，否则同一商品在 Excel 像两个 SKU。

### 5.4 channel 标签 (我们加的)

ttpos 物理层**没有 channel 概念**——堂食用 `ttpos_statistics_product`，外卖用 `ttpos_takeout_order_item`，是不同表。

我们在 `sale_event.py` 通过 UNION ALL 加 `'dine' AS channel` / `'takeout' AS channel` 字面量，**统一到一张语义表**。这是 ACL 最典型的应用。

## 6. 升级影响分析

ttpos 改字段时**怎么处理**：

### Scenario A: ttpos 改字段名 (e.g. `product_sale_price` → `unit_sale_price`)

**影响范围**：
- `sale_line.py:38` `SUM(product_sale_price * product_num)`
- `sale_event.py:31` 同上
- `price_breakdown.py:22` 同上

**修复**：3 处 SQL 改字段名，**不影响下游任何代码 / 文档**（因为下游用的是我们的 `sales_price`）。

### Scenario B: ttpos 改字段含义 (e.g. `actual_amount` 改成不扣赠送)

**影响**：转换规则要改 (sale_line.py:42-43)。如果财务对账锚还能通过 (TtposAnchorCheck)，下游不动。

### Scenario C: ttpos 删字段 (e.g. 取消 `member_order_discount_rate`)

**影响**：
- `sale_line.py:47` 算 `avg_member_discount` 失败
- 下游 `validators` 的 SANITY_BAND 可能不再触发某 identity

**修复**：评估这个指标是否仍需要；不需要就移除我们语义层的 `avg_member_discount`，下游一并清理。

### Scenario D: ttpos 加字段 (e.g. 加 `dish_category`)

**影响**：零 (我们不消费的字段对我们透明)。

**机会**：评估是否要在 entity 加新维度 (走 `/onboard-fact-table` skill)。

## 7. 解耦评估 (报表服务独立上线可行性)

### 7.1 当前依赖面

```
我们 vs ttpos 总依赖:
  10 张表
  ~50 个字段
  6 个转换规则 (时间过滤 / 软删 / 多语言 / channel / dynamic time / 套餐摊薄)
```

### 7.2 哪些是 hard dependency

| 依赖项 | 替代成本 | 备注 |
|---|---|---|
| BQ 同步 ttpos 数据 | 高 | 整个 BQ dataset 设计是 ttpos 的镜像 |
| 表名 `ttpos_*` 命名空间 | 中 | 改名要全局重 SELECT |
| `dataset = shop{uuid}` 分库规则 | 中 | 报表逻辑跨 53 个 dataset 并发 |
| `dynamic time condition` 算法 | 低 | ttpos 后端的算法选择，我们 copy 过来即可 |
| 字段名 (50 个) | 低-中 | 改 SQL 几行就改完 |

### 7.3 报表服务独立上线的可行性

**结论**：✅ **可行**，门槛在数据同步而非语义。

具体路径：
1. **保留 BQ 同步** (ttpos → BQ 不归我们管)
2. **本服务作为独立 product**：定义自己的 API / 文档 / 收费
3. **客户视角**：客户只看我们的 API + data-menu.md，**不需要知道 ttpos**
4. **ttpos 改字段时**：本服务团队改 entities CTE，对外契约不变

**未来上线时要先做的**：
- ✅ 已有：`data-contract.md` (本文档) — 合约
- ⚠️ 待做：报表 API (当前只有 CLI，没有 HTTP/gRPC 服务化)
- ⚠️ 待做：客户白名单 / 鉴权 / 限流
- ⚠️ 待做：SLA 承诺 (e.g. 月度报表 T+2 出, 准确度 < 0.1%)

## 8. ttpos 字段使用 — 没消费但有价值的

下面这些 ttpos 字段**我们目前没用**但**业务上有显著价值**，可作为后续扩展候选：

| 字段 | 表 | 业务价值 |
|---|---|---|
| `toi.ttpos_price` | takeout_order_item | 外卖标价 vs 实付价拆出"折让"明细 |
| `t.platform` | takeout_order | 区分 Grab / LINE MAN / Shopee 各自营收占比 |
| `t.merchant_charge_fee` | takeout_order | 商家小单费收入 (华莱士=0, 启用后必需) |
| `t.merchant_discount` | takeout_order | 商家承担优惠 (同上) |
| `t.platform_discount` | takeout_order | 平台贴钱促销 (推广策略效果) |
| `t.delivery_fee` | takeout_order | 配送费 (顾客实付分析) |
| `t.tax` | takeout_order | 税费 (法定财报必需) |
| `t.eater_payment` | takeout_order | 顾客实付 (跟 platform_total 差额分析) |
| `pp.category_uuid` | product_package | 商品分类维度 |
| `sp.member_order_discount_rate` | statistics_product | (已部分用) 会员折扣率分析 |

接这些字段走 `/onboard-fact-table` skill 流程。

## 9. 排障 / 升级速查

| 症状 | 看哪一节 |
|---|---|
| BQ 改 schema 后报表挂了 | §6 升级影响分析 |
| 客户问"你们消费了我们 ttpos 哪些数据" | §3 字段使用清单 + §2 表清单 |
| 客户想加新维度 (e.g. 按品类) | §8 没消费但有价值的字段 |
| 报表服务想独立上线 | §7 解耦评估 |
| ttpos 字段语义变化 | §5 转换规则 + §6 升级影响 |
| BQ 字段使用层踩坑 | [ttpos-bq-field-pitfalls.md](./ttpos-bq-field-pitfalls.md) |

## 10. 维护规则

**本文档 = 报表服务跟 ttpos 的契约**。

- ❗ 改 `semantic/entities/*.py` 时，**必须同步改本文档**第 3 章字段清单（走 `/sync-docs` skill）
- ❗ 加新表 / 新字段时，必须 update §3 + §4
- ❗ 改转换规则时，必须 update §5
- ❗ 报表服务独立上线前，本文档要跟 ttpos 团队 sign-off

## 相关文档

- [metrics-catalog.md](./metrics-catalog.md) — 工程师视角口径 (本文档下游)
- [data-menu.md](./data-menu.md) — 业务视角菜单 (本文档下游)
- [ttpos-bq-field-pitfalls.md](./ttpos-bq-field-pitfalls.md) — ttpos 字段陷阱
- [profit-report-takeout-semantics.md](./profit-report-takeout-semantics.md) — 外卖口径调研
- [.claude/skills/sync-docs/SKILL.md](../.claude/skills/sync-docs/SKILL.md) — 同步文档跟代码 skill
- [.claude/skills/onboard-fact-table/SKILL.md](../.claude/skills/onboard-fact-table/SKILL.md) — 接新表 skill
