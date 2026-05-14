# 利润报表对账清单 / F vs G 差值排查方法论

> 用于排查"营业额 F 跟 标价×销量 G 不等"的对账标准流程。
> 配套 `profit_margin.yaml` 的 F 列（营业额）和 G 列（标准金额）。
> 归档日期 2026-05-13。

## 前提矫正

**`list_price × qty = 营业额` 不是恒等式，不应该恒成立**。营业额永远 = 实际成交价 × 销量，标价是个"参考值"。两者相等只是大多数 SKU 没改过价、没差异化的巧合（实测 92.4%）。

公式拆解：

```
F − G  =  Σ堂食 (product_sale_price − pp.price) × product_num
       +  Σ外卖 (toi.price           − pp.price) × toi.quantity
```

**只要订单里的"实际成交价"跟"当前标价"不等，F − G ≠ 0**。这跟数据有没有问题没关系——大多数时候这是正常业务现象。

---

## 全集团基线（2026-04，56 家活跃店）

```
═══════════════════════════════════════════════════════════
全集团 2026-04   F − G 渠道拆解
═══════════════════════════════════════════════════════════
  堂食侧 净贡献       :  -67,270  (改价/促销让利 -71,325 + 反向 +4,055)
  外卖侧 净贡献       :  +11,886  (加价 +29,682 − 促销 -17,796)
  ───────────────
  全集团 F-G 净差     :  -55,384   (占营业额 -0.153%)

  ▶ 堂食侧让利绝对值 = 外卖侧加价收入的 2.4 倍
  ▶ 外卖侧整体在赚（+11,886），堂食侧整体在让利（-67,270）
═══════════════════════════════════════════════════════════
```

SKU 分布：

```
零差   F == G :  92.4% (9,691)   完全按 list_price 卖
正差   F  > G :   3.6% (  375)   外卖加价 / 堂食涨价
负差   F  < G :   4.0% (  424)   堂食改价 / 促销
```

---

## 影响因素全清单

### A. 堂食侧（让 `product_sale_price ≠ pp.price` 的全部原因）

| # | 因素 | 方向 | 在 ttpos 哪里发生 | 备注 |
|---|---|---|---|---|
| **A1** | **跨期标价被改过** | 双向 | 商品管理后台改 `pp.price` | **最隐蔽**——pp.price 反映扫描时的当前值，历史订单价不会回填 |
| **A2** | **多档销售价**（同 SKU 不同套餐/规格价格不同） | 双向 | 套餐/规格管理 | **已实证**：紫薯爆米花/矿泉水/鸡肉芝士球 |
| **A3** | **多规格/多单位**（大小份、按重量计价） | 双向 | 商品规格表 | 散装、称重商品 |
| **A4** | **会员/促销固定价直接录入 `product_sale_price`** | 一般负 | 营销活动后台 | 与 A5 不同：这个直接改了销售价，不是后续扣减 |
| **A5** | **打折调价**（final_price < sale_price） | **不影响 F−G** | 收银时折扣 | 差额进 `discount_amount`，进 H 不进 F |

### B. 外卖侧（让 `toi.price ≠ pp.price` 的全部原因）

| # | 因素 | 方向 | 在 ttpos 哪里发生 | 备注 |
|---|---|---|---|---|
| **B1** | **外卖差异化定价**（堂 59 / 外 69） | 正差 | 外卖菜单后台 / Grab/LINE MAN 商家端 | **集团策略主力**，已实证 5 个核心 SKU |
| **B2** | **外卖促销让利**（外卖按低价卖） | 负差 | 平台联合促销 | 如"超值套餐5" 堂 119 / 外 112 |
| **B3** | **外卖历史改价** | 双向 | 同 A1 | pp.price 后被改，toi.price 不变 |
| **B4** | **平台显示价 vs POS 标价**（converter 直接取平台） | 双向 | grab/lineman converter | converter 阶段不查 POS 标价系统 |
| **B5** | **state=60 取消订单** | **已排除** | SQL 过滤 | F 排除了，G 也排除了，**不影响 F−G** |

### C. 数据/口径层面（容易踩坑）

| # | 因素 | 表现 | 怎么查 |
|---|---|---|---|
| C1 | **pp.price = 0 / NULL**（标价没填） | G 恒为 0 | 2026-04 全集团扫过 0 行，✅ |
| C2 | **跨店共用同 SKU 不同标价** | 跨店对比时 G 不对齐 | 店内独立计算，不跨店聚合 |
| C3 | **删除商品**（pp.delete_time > 0） | pp.price 仍能查到（删除前最后值） | 历史订单可能跟实际不符 |
| C4 | **product_package vs SKU 多对一** | 多 uuid 同 name 不同价 | 已实证：shop6977459593216000 巨型脆皮鸡 |
| C5 | **退款 / 赠送 / 赠品** | **不影响 F−G** | F 和 G 两边都含退款件 |
| C6 | **币种/单位** | 全 THB 元（不是分） | 已验证 ✅ |

### D. 不影响 F − G 的因素（容易误认为有关）

| # | 因素 | 为什么不影响 |
|---|---|---|
| D1 | 退款 (refund_num) | F 用 sale_price × num，G 用 list_price × num，num 都含退款 |
| D2 | 赠品 / 赠送 (free / give) | num 都含，价格都用 sale_price，相互抵消 |
| D3 | 折扣 (sale_price → final_price) | 差额进 discount_amount，不进 F |
| D4 | 外卖取消 (state=60) | SQL 两边都过滤 |
| D5 | 平台抽佣 (commission) | ttpos 不采集，不在 F 也不在 G |
| D6 | 配送费 / 平台优惠 / 税 | 都不进 F 和 G |

---

## 对账排查标准流程（5 步）

### 1. 渠道分布判定

按 SKU 看 `dine_qty` vs `take_qty`：

- 几乎纯堂食 (take_qty < 5%) → **A 类因素**
- 几乎纯外卖 (dine_qty < 5%) → **B 类因素**
- 两者都有 → **拆 dine_sales / take_sales 分别看**（需路径 3 拆 channel 列）

### 2. 堂食侧 sub-验证

看 `list_price × dine_qty` vs `dine_sales`：
- 相等 → 堂食按标价卖，差额全在外卖
- 不等 → A1/A2/A3/A4 之一

### 3. 外卖侧 sub-验证

看 `list_price × take_qty` vs `take_sales`：
- 相等 → 外卖按标价卖，差额全在堂食
- 大于 → **B1 外卖加价**
- 小于 → **B2 外卖促销**

### 4. 跨店一致性判定

- **多店同方向同金额差** → 集团统一策略（如番茄炸鸡桶 56 店一致 -42k）
- **个别店独有** → 单店自主行为
- **方向矛盾**（同一 SKU 有店正有店负）→ 数据异常或多店策略不一致
  - 2026-04 实测扫到 0 个方向矛盾 SKU，说明集团策略执行非常一致

### 5. 跨期复现判定

跨月跑相同 SKU：
- 差值在月间漂移但 SKU 还在 → 怀疑 **A1/B3 pp.price 被改过**
- 差值稳定 → 业务结构性差异，不需修复
- 突然出现/消失 → 营销活动起止

---

## 已知集团策略与异常清单（2026-04）

### 集团外卖加价清单（B1）

| SKU | 总店 | 加价店 | 一致率 | Σ 加价收入 |
|---|---:|---:|---:|---:|
| 炸手枪腿饭 | 56 | 52 | 93% | +4,420 |
| 脆皮手枪腿 | 56 | 51 | 91% | +10,200 |
| 鸡米花饭 | 47 | 45 | 96% | +3,200 |
| 鸡肉芝士球（TH） | 56 | 37 | 66% | +2,100 |
| 紫薯脆片 | 56 | 35 | 63% | +2,010 |

### 集团堂食促销清单（A4）

| SKU | 总店 | 改价店 | 一致率 | Σ 改价损失 | 平均让利 |
|---|---:|---:|---:|---:|---:|
| **番茄炸鸡桶** | 56 | **56** | **100%** | **-42,237** | 标 139 / 卖 128~129 |
| 鸡肉棒 | 112 | 56 | 50% | -19,020 | — |
| 番茄薯条 | 108 | 84 | 78% | -12,205 | — |
| 番茄鸡米花 | 56 | 56 | 100% | -5,890 | — |

### 反向异常清单（值得追查）

#### 单店异常 (shop6977459593216000)

| SKU | 标价 | 堂均价 | 反向 Δ | 推测 |
|---|---:|---:|---:|---|
| 巨型脆皮鸡 | 79 | **164.20** | +426 | **C4 多 uuid 同 name**？或 A1 跨期改价 |
| 脆皮鸡腿 | 59 | **147.00** | +88 | 同上 |
| 香辣鸡肉汉堡 | 79 | **149.00** | +70 | 同上 |
| 香辣鸡腿堡 | 60 | 65.25 | +892 | **单店自主调价**（170 件统一 +5） |

#### 多店共性 (A2 多档销售价)

| SKU | 反向店数 | Σ 反向 | 标价 | 堂均价范围 |
|---|---:|---:|---:|---|
| 紫薯爆米花 (มันม่วง) | 12 | +780 | 49 | 50~58 |
| 矿泉水 | 20 | +440 | 15 | 15~18 |
| 鸡肉芝士球 | 8 | +280 | 49 | 49~59 |

特征：标价始终是最低档，堂均价在标价之上 1~10 元——**疑似多档销售价 + pp.price 只记最低档**。

---

## 配套校验脚本（参考）

需要重跑 F − G 渠道拆解时（每月对账可执行）：

```python
# 取自 docs/profit-margin-reconciliation-checklist.md 配套脚本
# 1) 跑下面的 SQL 模板替换 {project}/{dataset}/{start_ts}/{end_ts}
# 2) 输出 (堂食贡献, 外卖贡献) 用于对账

with dine as (
  select product_package_uuid as item_uuid,
         sum(product_num) as qty,
         sum(product_sale_price * product_num) as sales
  from `{project}`.`{dataset}`.`ttpos_statistics_product`
  where complete_time >= {start_ts} and complete_time < {end_ts}
    and product_num > 0
  group by item_uuid
),
take as (
  select toi.ttpos_product_package_uuid as item_uuid,
         sum(if(t.order_state in (10,20,30,40), toi.quantity, 0)) as qty,
         sum(if(t.order_state in (10,20,30,40), toi.price*toi.quantity, 0)) as sales
  from `{project}`.`{dataset}`.`ttpos_takeout_order_item` toi
  join `{project}`.`{dataset}`.`ttpos_takeout_order` t
    on t.uuid = toi.takeout_order_uuid and t.delete_time = 0
  where toi.delete_time = 0 and toi.ttpos_product_package_uuid > 0
    and t.order_state in (10,20,30,40) and t.accepted_time > 0
    and ((t.order_state = 40 and t.completed_time >= {start_ts} and t.completed_time < {end_ts})
      or (t.order_state != 40 and t.accepted_time >= {start_ts} and t.accepted_time < {end_ts}))
  group by item_uuid
)
select
  sum(ifnull(d.sales,0) - pp.price * ifnull(d.qty,0)) as dine_contrib,
  sum(ifnull(t.sales,0) - pp.price * ifnull(t.qty,0)) as take_contrib,
  sum(ifnull(d.sales,0) + ifnull(t.sales,0)
    - pp.price * (ifnull(d.qty,0) + ifnull(t.qty,0))) as total_diff
from `{project}`.`{dataset}`.`ttpos_product_package` pp
left join dine d on d.item_uuid = pp.uuid
left join take t on t.item_uuid = pp.uuid
where d.qty > 0 or t.qty > 0
```

---

## 相关文档

- [profit-report-takeout-semantics.md](./profit-report-takeout-semantics.md) — 外卖口径调查（含 platform_total 跟 toi.price 关系）
- [takeout-revenue-calculation.md](./takeout-revenue-calculation.md) — 外卖营业额对账报表（独立线，不是利润报表）
- [bigquery-export-guide.md](./bigquery-export-guide.md) — BQ 导出总入口
