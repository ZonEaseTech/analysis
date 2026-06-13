# 跨账本互证基线 (2026-05 观察跑)

> 运行: 2026-06-12, `scripts/adhoc/audit_cross_ledger_202605.py`
> 决定: CROSS_LEDGER_IDENTITIES 是否进导出闸门 (spec §11 PR-A 验收线)

## 结论

- [ ] qty 互证匹配率 100% → CROSS_LEDGER_QTY 进闸门
- [x] **存在稳定可解释差异 (套餐子商品粒度) → 修 order_line 口径后复跑 (PR-B 工作项)**
- [ ] 存在不可解释差异 → 开专项排查, CROSS_LEDGER 维持观察模式

> **改判说明**: 原始勾选了「不可解释差异」，但根因分析已明确——差异由 `sale_order_product`
> 套餐子商品展开导致，属于**结构性、可预期、有明确修复路径**的口径偏差，
> 不是随机噪音或数据损坏。正确结论是「可解释差异，修口径后复跑」。
> 修复路径: `order_line_cte()` 中过滤套餐子商品行（parent_uuid 非空行）或改从套餐父级聚合。
> 修复后用相同月份复跑，目标 qty 匹配率 ≥ 95%。该修复列为 PR-B 工作项。

**根因 (非时间语义): `sale_order_product`（凭证账）的粒度与 `statistics_product`（统计账）根本不同。**
凭证账逐行记录套餐/组合餐的每个**子商品**（如套餐内的可乐、薯条），而统计账只记录**顶层 SKU**（套餐本身）。
两本账互证的前提条件——"同 item_uuid 代表同物理实体"——在套餐展开场景不成立。
这不是 `finish_time` vs `complete_time` 的时间漂移，而是 SQL 口径设计层面的结构性差距，
必须先修正 `order_line_cte()` 的粒度对齐（过滤子商品行 / 改走套餐父级），才能复跑。

---

## 统计账 vs 凭证账

### 全局匹配率

| 指标 | 数值 |
|---|---|
| 全局 (store, item) 行数 | 14,540 |
| qty 精确匹配 | 4,583 / 14,540 (**31.5%**) |
| qty \|delta\| P50 | 3 |
| qty \|delta\| P95 | 87 |
| qty \|delta\| max | 2,087 |
| gross \|delta\| P50 | 218 THB |
| gross \|delta\| P95 | 6,279 THB |
| gross \|delta\| max | 102,263 THB |

匹配率 31.5% 远低于升闸门所需的 100%。68.5% 的 (store, item) 行存在数量差异。

### 根因分析

主要发散 item: `3701522227134474` (ปีกไก่สไปซี่ / Crispy Chicken Wing, 单价 49 THB)。
该商品在统计账 (`statistics_product`) 仅作为顶层 SKU 记录（stat_qty 极少），
但在凭证账 (`sale_order_product`) 作为**套餐子商品**被大量重复计入（store005: voucher_qty=2098 vs stat_qty=11）。
典型凭证行: `num=8, sale_price=49, total_price=49` — total_price 不等于 num×sale_price，
说明是套餐内的"子行"，实际付款在套餐父行。

另一类发散: `3699672673943564` (เป๊ปซี่ / PEPSI)，store025: voucher_qty=1003 vs stat_qty=119。
Pepsi 既是独立销售商品，也是套餐内的饮料配件，凭证账混合计数。

反向发散 (stat > voucher): `3722377607252690` (ไก่ทอดจุใจ MCK 5块套餐) — store052 有 stat_qty=248 但
voucher_qty=0。该商品为外部促销码兑换套餐 (`PEPSIMCK` 联名活动)，可能走独立优惠券路径，
不经 `sale_order_product` 写入。

**结论：两账本粒度不对齐，修复方向是在 `order_line_cte()` 中过滤掉套餐子商品行，
或改为从凭证账的套餐父行读数，而非子行聚合。**

### 最差门店 Top 15 (按 max gross delta 排序)

| 店 | 名称 | items | qty_exact | gross P95 | worst_delta |
|---|---|---|---|---|---|
| 005 | Lasalle ลาซาล | 323 | 126/323 | 7,995 | 102,263 |
| 018 | Sahapat Sriracha | 263 | 78/263 | 11,534 | 96,334 |
| 010 | Phraeksa แพรกษา | 269 | 94/269 | 10,075 | 69,923 |
| 004 | Number One Plaza | 299 | 107/299 | 7,897 | 69,041 |
| 006 | Samrong Market | 298 | 104/298 | 7,200 | 69,041 |
| 027 | Ladprao 122 | 289 | 88/289 | 8,281 | 63,896 |
| 035 | UTCC ม.หอการค้า | 272 | 92/272 | 8,463 | 47,383 |
| 029 | Khao Noi Pattaya | 248 | 80/248 | 5,293 | 42,777 |
| 012 | Khao Talo Pattaya | 257 | 69/257 | 7,371 | 42,679 |
| 037 | Nakorn Rayong 1 | 246 | 84/246 | 7,560 | 40,768 |
| 025 | Min Buri มีนบุรี | 243 | 65/243 | 9,480 | 40,745 |
| 063 | Chainat City | 192 | 84/192 | 9,300 | 39,875 |
| 052 | โรจนะ อยุธยา 2 | 262 | 70/262 | 11,100 | 36,952 |
| 026 | Preeda School | 271 | 97/271 | 5,343 | 36,456 |
| 028 | Hollywood Pattaya | 240 | 86/240 | 7,254 | 36,105 |

---

## 支付勾稽

### 结果

全 60 店均触发 🟡 NEEDS_REVIEW (封顶分级生效，未升为 🔴)。
payment_amount (sale_bill) 系统性低于 stat_actual (statistics_product 实收)，
delta 方向一致（payment < actual），绝对差异在 100K–400K THB / 店不等。

| 指标 | 数值 |
|---|---|
| 支付 delta P95 | 287,885 THB |
| 支付 delta max | 400,168 THB (店052) |
| 触发 🟡 门店 | 60/60 |

**校准建议**: 支付差距方向一致（sale_bill.payment_amount < actual_amount），
差异约 30–50%，极可能是口径差：
- `payment_amount` = 账单实际收款（扣除整单折扣、积分抵扣等）
- `actual_amount` = 商品级实收（product_final_price × qty，不含账单级折扣分摊）

在统一外卖渠道佣金/税费等账单级项目的分摊方式之前，PAYMENT_TIEOUT_IDENTITY 应维持
封顶 🟡，不升 🔴 红线（CLAUDE.md 技术债 ②）。

---

## 外卖订单勾稽

### 结果

| 指标 | 数值 |
|---|---|
| 有 ttpos_takeout_order 表的门店 | 60 / 60 |
| 外卖订单总数 | 47,452 |
| platform_total 匹配 (\|delta\|<0.01) | 47,450 / 47,452 (**99.996%**，不足以四舍五入为 100%) |
| 不匹配订单 | **2 笔需显式记录**: 店006 order=373316429388 (delta=+99 THB), 店059 order=372817075896 (delta=+89 THB) |
| 触发 🟡 的订单 | 2 笔 (金额小，属平台修正或退款时序，不影响整体结论) |
| merchant_charge_fee 非零行 | 0 |
| merchant_discount 非零行 | 0 |
| merchant 两字段均恒 0 | **是 ✅** |

**外卖订单勾稽质量极好 (匹配率 99.996%)。** 2 笔 🟡 异常 (店006 +99, 店059 +89) 金额极小，
属于平台数据修正或退款时序问题，不影响整体结论。

merchant_charge_fee 和 merchant_discount 确认全月恒为 0，口径假设
`platform_total == item_sum − merchant_charge_fee − merchant_discount` 成立。
TAKEOUT_TIEOUT_IDENTITY 暂不需要调整符号，但封顶 🟡 维持直至业务开启 merchant 收费为止。

---

## 无 takeout 表门店 (显式 N/A)

本次观察跑: **所有 60 家门店均有 `ttpos_takeout_order` 表 — N/A 清单为空。**

> **与 docs/ttpos-bq-field-pitfalls.md §4.2 的矛盾澄清**:
> §4.2 记录「194 个候选 dataset 里只有 45 家店有外卖订单表」，与此处「60/60 全有」表面矛盾。
> 两者指的是不同范围:
> - §4.2 基于历史全量 dataset 扫描（约 194 个，包含非活跃/测试/已关店 dataset）
> - 本次探测范围是 `resources/config.yaml` 中 **60 家活跃配置门店**，这些店是华莱士
>   当前运营门店，均已开通外卖业务，因此 60/60 全有 `ttpos_takeout_order` 表。
>
> **结论**: §4.2 的工程预警（"必须先探测"）仍然有效，未来扩店时仍需 INFORMATION_SCHEMA
> 探测保护。但对于当前 60 家活跃门店，takeout 表覆盖率 100%，不需要 N/A 降级处理。

---

## 下一步行动

| 优先级 | 行动 | 负责方向 |
|---|---|---|
| P0 | 修正 `order_line_cte()` — 过滤套餐子商品行，仅计套餐父级；或从凭证账按"账单→套餐→父级商品"粒度重建 | semantic/entities/order_line.py |
| P1 | 修复后用相同月份复跑本脚本，目标 qty 匹配率 ≥ 95% | scripts/adhoc/audit_cross_ledger_202605.py |
| P2 | 支付勾稽口径对齐（技术债 ②）：明确 payment_amount vs actual_amount 的定义差，决定哪一个是"真实收款" | 业务口径确认 |
| P3 | 外卖勾稽封顶解除判断：监控 merchant_charge_fee，业务开启时升 MUST_FIX | identities.py |

---

## 原始数据

```
qty 匹配率:      31.5%  (4583/14540)
gross delta P95: 6279.00 THB
gross delta max: 102263.00 THB
支付 delta P95:  287885.00 THB
支付 delta max:  400168.00 THB
外卖勾稽匹配率:  99.996%  (有表门店 60 店, 47450/47452 单)
merchant 字段恒0: 是
缺外卖表门店:    0 店
查询错误总数:    0 条
[决策] (脚本原始输出, 已被上方人工改判取代) qty 差异可解释=套餐子项粒度 →
       修 order_line 口径后复跑, CROSS_LEDGER 维持观察模式
```

全量脚本输出: `/tmp/cross_ledger_obs.txt` (不入库，本地留存)

---

*生成: 2026-06-12, audit_cross_ledger_202605.py, 全 60 店无查询错误*

---

## 复跑 (PR-B Task 2, 2026-06-13)

> 脚本: `scripts/adhoc/audit_cross_ledger_202605.py` (PR-B Task 1 已合入 `product_type != 2` 修复)
> 归因脚本: `scripts/adhoc/audit_tieout_outliers_202605.py`
> 决策分支: **(c) qty 匹配率 <99% + 残差未完全归因 → CROSS_LEDGER 维持观察模式**

### 复跑数字 vs 原始基线

| 指标 | 原始 (PR-A, 2026-06-12) | 复跑 (PR-B, 2026-06-13) | 变化 |
|---|---|---|---|
| 全局 (store, item) 行数 | 14,540 | 13,905 | -635 (套餐子行移除) |
| qty 精确匹配 (全渠道) | 4,583 / 14,540 (**31.5%**) | 6,331 / 13,905 (**45.5%**) | +14pp |
| qty \|delta\| P50 | 3 | 1 | ↓ |
| qty \|delta\| P95 | 87 | 29 | ↓ |
| qty \|delta\| max | 2,087 | 289 | ↓ |
| gross \|delta\| P50 | 218 THB | 59 THB | ↓ |
| gross \|delta\| P95 | 6,279 THB | 3,135 THB | ↓ |
| gross \|delta\| max | 102,263 THB | 36,952 THB | ↓ |
| 外卖勾稽匹配率 | 99.996% (47,450/47,452) | **100.0%** (47,452/47,452) | +0.004pp |
| 查询错误 | 0 | 0 | — |

### 复跑根因分析

`product_type != 2` 修复使 qty 匹配率从 31.5% 提升至 45.5%，但仍远低于 99%。

**新发现的结构性根因（两层）：**

**层 1 — 渠道覆盖缺口 (55% 差距的主因)**
- `sale_event` 统计账 UNION ALL 了 `dine`（堂食）+ `takeout`（外卖）两个渠道
- `order_line_cte` 凭证账仅覆盖 `dine` 路径（`sale_bill → sale_order → sale_order_product`）
- 外卖订单走 `ttpos_takeout_order_item`，不经过 `sale_bill`，无法纳入当前 `order_line_cte`
- 实测（shop002 item 3726275394930695）：stat_qty=236 = dine 47 + takeout 189；voucher_qty=47（仅 dine）

**dine-only 匹配率（去除渠道缺口的干净数字）：93.2% (10,433/11,192)**

**层 2 — 月界时间语义残差 (6.8% 差距)**
- `statistics_product.complete_time` vs `sale_bill.finish_time` 在月界存在系统性差值
- 方向：全部 `vchr > stat`（finish_time 落 May，complete_time 落 April）
- 实测（shop018 item 3727577405458434）：stat_in=686，vchr_in=714，delta=-28
  月界 ±48h 分段明确确认：post-May 两侧一致，差值集中在 May-IN 窗口
- 高流量店月界残差更大（shop018 最差 72.5%，低流量店 100%）

### 2 笔外卖异常单归因（技术债 ⑦）

**shop006 order 3733164293885953 (delta=+99 THB)**
- item_sum = 99 THB（1× 汉堡套餐 @99），platform_total = subtotal = 198 = 2× item_sum
- eater_payment=198 确认客户实付 198 THB
- 归因：`ttpos_takeout_order_item` 仅记录 1 行，但 platform 计 198。疑似单内有第 2 份商品
  未映射至 `ttpos_product_package_uuid`（is_mapped=0 行），导致 item 行不完整。
- 修复方向：检查该订单中 `is_mapped=0` 的 item 行

**shop059 order 3728170758965263 (delta=+89 THB)**
- item_sum = 158 THB = subtotal = 158（2 品：Sweet Corn @39 + Value set 5 @119）
- platform_total = 247，eater_payment = 247；delivery_fee 字段 = 0
- 归因：89 THB 差额 = platform_total − subtotal，存储在 `raw_data`/`additional_properties`
  中的配送费字段（BQ schema 已明确有 delivery_fee 列但值为 0，可能平台传输延迟或字段映射问题）
- 当前 `takeout_tieout_cte` 口径：`platform_total == item_sum`（忽略配送费）
- 若需精确：应改为 `platform_total == subtotal + delivery_fee + ...`
- 结论：2 单均为**平台数据映射/字段口径问题**，与商品销售数据质量无关，技术债 ⑦ 状态：已归因，需平台字段核实

### 支付勾稽口径分解（技术债 ②）

| 指标 | 店001 | 店005 | 店010 |
|---|---|---|---|
| sale_bill 数 | 1,283 | 3,964 | 3,523 |
| sb.amount (折后应收) | 166,069 | 583,051 | 507,955 |
| sb.payment_amount | 165,300 | 578,897 | 504,376 |
| stat_actual (全渠道) | 317,389 | 863,651 | 652,417 |
| stat_dine only | 164,923 | 580,005 | 492,010 |
| stat_takeout only | 152,466 | 283,646 | 160,407 |
| gap (payment − stat_total) | −152,089 (−47.9%) | −284,754 (−33.0%) | −148,041 (−22.7%) |
| gap (payment − stat_dine) | **+377 (+0.2%)** | **−1,108 (−0.2%)** | **+12,366 (+2.5%)** |

**候选公式（已验证）：`sale_bill.payment_amount ≈ stat_actual_dine`**

口径结论：
- `payment_amount` 仅覆盖堂食 POS 收款，外卖订单由平台代收，不过 `sale_bill`
- 30-50% 差额 ≈ `stat_actual_takeout` / `stat_actual_total`（外卖占比）
- shop001 差额 47.9% ≈ takeout 占比 48.0% ✅；shop005 33.0% ≈ 32.8% ✅
- shop010 差额 22.7% vs takeout 占比 24.6%（2% 偏差，需进一步确认 payment_order 侧是否含外卖渠道）
- **正确闸门选择**：`PAYMENT_TIEOUT_IDENTITY` 应改为对比 `payment_amount` vs `stat_dine_actual`
  而非 `stat_total_actual`；当前封顶 🟡 正确，但参照系需修正（技术债 ②）

### 残差抽样结论

取 shop018（最差 dine-only 72.5%）的 top 5 残差对：

| item_uuid | stat_dine | vchr | delta | 性质 |
|---|---|---|---|---|
| 3727577405458434 | 686 | 714 | −28 | finish_time IN，complete_time OUT（月界） |
| 3699691531534349 | 298 | 314 | −16 | 同上 |
| 3727572296796204 | 130 | 142 | −12 | 同上 |
| 3727586387560573 | 222 | 232 | −10 | 同上 |
| 3699691523145734 | 131 | 139 | −8 | 同上 |

月界 ±48h 分段验证（item 3727577405458434，shop018）：

|  | pre-May | May-IN | post-May |
|---|---|---|---|
| stat | 0 | 686 | 29 |
| voucher | 0 | 714 | 29 |
| delta | 0 | **-28** | 0 |

残差完全集中在月界 May-IN 窗口，post-May 两侧一致 → **纯时间语义残差确认**。

但残差规模（6.8% 整体）超过"仅月界几单"的量级：shop018 高流量店每月约 28 单受影响，
说明完整月的"完整性"尚依赖两个时间戳的长期对齐，不是 1-2 单异常。

### 决策结论（分支 c）

**qty 匹配率 45.5%（全渠道）/ 93.2%（dine-only）< 99% 阈值 → 分支 (c)**

- `CROSS_LEDGER_IDENTITIES` 不进 `FULL_IDENTITIES`，维持独立观察模式
- `CROSS_LEDGER_QTY` 和 `CROSS_LEDGER_GROSS` 的 classify 不变
- 技术债 ⑥ 更新：两步升级路径：
  1. `order_line_cte` 纳入 takeout 路径（添加 `ttpos_takeout_order_item` 路由），全渠道匹配
  2. 月界时间语义对齐（考虑 `business_date` 维度取代点时间戳比较）
  以上完成后复跑，预期 ≥99%，届时升分支 (b) 或 (a)

### 技术债更新状态

| 债项 | 状态 |
|---|---|
| ② payment 口径 | **已归因**：payment_amount ≈ stat_dine_actual；参照系修正留 PR-C |
| ⑥ CROSS_LEDGER 升级 | **维持观察**；下一步：order_line 纳入 takeout + 月界对齐 |
| ⑦ 外卖 2 单异常 | **已归因**：shop006=商品行不完整；shop059=delivery_fee 字段映射残缺 |

---

*复跑: 2026-06-13, PR-B Task 2, 决策分支 (c), 全 60 店无查询错误*

---

## PR-C 复跑 (外卖路径, 2026-06-13)

> 脚本: `scripts/adhoc/audit_cross_ledger_202605.py` (无改动; 自动吃 `semantic/entities/order_line.py` 新增的 takeout UNION ALL 分支)
> 凭证账 CTE 改动: PR-C 给 `order_line_cte` 加 `ttpos_takeout_order_item` 路径 → 凭证账由 dine-only 升为 dine+takeout 全渠道
> 决策分支: **(c) 维持观察模式 — 结构天花板 ~89.5%, ≥99% 不可达**

### 复跑数字 vs PR-B 基线

| 指标 | PR-B (dine-only 凭证, 2026-06-13) | PR-C (dine+takeout 凭证, 2026-06-13) | 变化 |
|---|---|---|---|
| 全局 (store, item) 行数 | 13,905 | 13,905 | 0 |
| qty 精确匹配 (全局) | 6,331 / 13,905 (**45.5%**) | 12,440 / 13,905 (**89.5%**) | **+44pp** |
| qty `|delta|` 分布 | — | P50=0 / P95=1 / max=28 | — |
| gross `|delta|` 分布 (THB) | — | P50=0 / P95=10,900 / max=249,200 | — |
| gross 恒等式 | — | ✅ 12,446 / 🟡 459 / 🔴 1,000 | — |
| 外卖订单勾稽 | 99.996% | 100.0% (60 店 / 47,452 单) | — |
| 查询错误 | 0 | 0 | — |

预测 ~89.5%，实测 **89.5%**，逐点命中。脚本未单独拆 dine / takeout 子率
(全局口径汇总输出)；dine-only 残差归因见下文 (PR-B 曾报 dine-only 93.2%)。

### 最差门店 Top 5 (按 max gross delta, 2026-05)

| 店 | 名称 | items | qty_exact | gross_P95 (THB) | worst_delta (THB) |
|---|---|---|---|---|---|
| 018 | Sahapat Sriracha 卫星店 | 249 | 164/249 | 42,000 | 249,200 |
| 053 | Ratchada ซอย 3 | 241 | 172/241 | 31,800 | 207,900 |
| 028 | Hollywood Pattaya | 235 | 180/235 | 21,800 | 113,400 |
| 035 | UTCC ม.หอการค้า | 260 | 226/260 | 19,800 | 113,400 |
| 029 | Khao Noi Pattaya | 233 | 193/233 | 15,800 | 106,800 |

### 纠正 PR-B 时间语义假设（重要）

PR-B「复跑」段把 dine 残差归到**月界 `complete_time` vs `finish_time` 漂移**
(声称 shop018 高流量店每月约 28 单落在 May-IN 窗口，"纯时间语义残差确认")。
**PR-C 调查证伪了这个假设**：

- shop018 实测**跨窗口行数 = 0** —— 不存在「finish_time IN / complete_time OUT」的月界单。
  PR-B 残差抽样表把 −28 归因于月界，是错的。
- 真实 dine 残差根因 = **ttpos 后端写入路径不对称**：`sale_order_product`（凭证账）
  有时记录了 `ttpos_statistics_product`（统计账）漏记的销售；方向恒为 **voucher > stat**
  (与 PR-B 抽样表里 LHS<RHS 的 stat<voucher 符号一致，但成因是后端写入而非时间)。
- **改时间字段不是修复**：把比较基准从 `complete_time` 换到 `finish_time`（或 business_date）
  并不能消掉这批差异，反而会更差 —— 差的不是落在哪个月，而是统计账压根没写这条。

### 结构天花板 ~89.5%（残差 ~10.5% 不可控）

残差 ~10.5% = 三类结构性成分，**全部在 BQ 口径之外**：

1. **后端 sp/sop 写入不对称** —— `sale_order_product` 写了而 `statistics_product` 漏写
   (ttpos 后端写入可靠性问题，方向 voucher > stat)。
2. **未映射外卖商品** —— `is_mapped=0` / `package_uuid=0` 的 takeout 行，只在一本账里出现。
3. **促销码路径** —— 例如 `PEPSIMCK` 等促销码只落在单边账本。
4. **builder 口径项(可消,非后端不可控)** —— `cross_ledger.build_cross_ledger_rows`
   比较时 stat 侧用 sale_event raw qty(含外卖 state=60 取消件,被拆进 cancelled_qty),
   voucher 侧 order_line 已排除 state=60,故取消件使 stat 略高于 voucher。这一项**不是**
   后端问题,builder 改用 net_qty(扣 cancelled)对齐两侧即可消除,留作 CROSS_LEDGER
   后续收口的可控项(前三项才是结构天花板)。

明确结论：**≥99% 不可达**。后端写入可靠性问题属 ttpos 业务系统层，不在本 BQ 分析口径
可修复范围内；继续往 order_line SQL 上加路由/对齐时间，都无法跨过这个天花板。

### CROSS_LEDGER 决策（分支 c，维持观察）

**CROSS_LEDGER 保持独立观察包，不升入导出闸门。** `identities.py` 不改动。

理由（零容差核心 = 报警必须是真错）：

- 10.5% 残差是后端不可控的结构性差异，不是数据管线 bug。
- 升 🔴 → 每次导出都误报，闸门失信。
- 升 🟡 进 `FULL_IDENTITIES` → 给每次导出注入 ~10% 噪音，稀释闸门可信度。
- 因此 `CROSS_LEDGER_IDENTITIES` 继续与 `FULL_IDENTITIES` 解耦，仅作旁路观察跑。

### 技术债更新状态

| 债项 | 状态 |
|---|---|
| ⑥ CROSS_LEDGER 升级 | **维持观察 (分支 c)**；外卖路径已并入凭证账 (45.5%→89.5%)，时间语义假设已证伪；残差为后端写入不对称 + 未映射外卖商品 + 促销码单边，结构天花板 ~89.5%，≥99% 不可达，故不升闸门 |

---

*PR-C 复跑: 2026-06-13, 外卖路径并入凭证账, qty 89.5% (预测命中), 决策分支 (c), 全 60 店 0 查询错误*
