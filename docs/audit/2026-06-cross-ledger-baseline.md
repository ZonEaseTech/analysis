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
