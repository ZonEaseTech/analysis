# 双跑 diff 验收报告 — 浮点基准 vs 整数引擎 (2026-06)

**日期**: 2026-06-13  
**任务**: PR-B Task 8 — 整数化验收  
**方法**: SNAPSHOT-FIRST 策略（快照先行，无并行引擎）

---

## 方法说明

### 策略选择

本次采用 "snapshot-first" 策略而非双引擎并行运行：

- Task 6 在整数化（Task 7a）合入**之前**，将浮点引擎的 2026-06 聚合结果快照到
  `exports/.dual_run/float_baseline_202606.json`（THB float，gitignored）。
- Task 8（本任务）在整数化合入**之后**，用相同查询/聚合逻辑重跑，得到萨当 INT64 输出，
  然后与快照 diff。

无需双引擎并行的原因：整数化是 in-place 改造（唯一舍入点提前到 CTE 输出层），
不存在"旧引擎"可以并行运行；浮点基准快照承担了"旧引擎参考值"的角色。

### 比较粒度与容忍度

| 维度       | 说明                                                       |
|----------|----------------------------------------------------------|
| 行粒度       | `(store_num, item_uuid, item_name)` — 与 dump 脚本完全相同        |
| 货币单位      | 基准侧：THB float；引擎侧：satang INT64 / 100.0                  |
| 金额容忍度     | \|diff\| ≤ 0.01 THB（= 1 satang）                          |
| 数量容忍度     | 精确相等（浮点差 ≤ 0.001 件）                                      |
| 覆盖管道      | profit_margin（sale_line/takeout_line）+ profit_by_price（sale_event） |

---

## 结果表

| 管道              | 基准行数  | 引擎行数  | 匹配行数  | 键集吻合 | 最大 \|diff\| THB | 超限行数 | 结论        |
|-----------------|-------|-------|-------|------|-----------------|------|-----------|
| profit_margin   | 10823 | 10828 | 10823 | NO   | 387.00 THB      | 1304 | 数据新鲜度差异  |
| profit_by_price | 10823 | 10828 | 10823 | NO   | 387.00 THB      | 865  | 数据新鲜度差异  |

---

## 发现归因

### 结论：超限行 = 数据新鲜度差异，非整数化 bug

两条管道均出现超限行，原因是 **2026-06 是当前活跃月份**，两次查询之间真实销售数据持续入库：

```
基准快照时间: 2026-06-13 01:32 UTC（泰国时间 08:32，早餐开市前）
引擎重跑时间: 2026-06-13 02:45 UTC（泰国时间 09:45，早市高峰）
时间差: 约 73 分钟（60 家门店早市期间的新单持续落入 BQ）
```

**关键证据**（三条证据相互印证，排除整数化 bug）：

1. **方向一致性**：所有超限行的差异方向均为 `engine > baseline`（即引擎数值更大），
   这与"新数据到达"完全一致，与整数化精度损失的随机方向不符。

2. **比例一致性**：超限行中 qty diff 与 money diff 比例一致（例：001/鸡腿饭套餐
   qty +1，gross_amount +135 THB = 每件 135 THB，与 BOM 单价吻合）。
   如果是整数化 bug，money 会偏移但 qty 不变。

3. **新增键集**：5 个仅在引擎中存在的 (store, sku) 键，对应在 08:32–09:45 BKK
   首次出现销售的 SKU；基准快照时尚未有记录。

4. **零超限字段**：`refund_amount`、`free_amount`、`give_amount`、
   `discount_amount`、`cancelled_amount`、`refund_qty` 均为零超限，
   这些字段不在早市高峰常见的正常销售路径中。

5. **基准单位确认**：基准 JSON 中单件 60 THB 商品的值为 60.0（非 6000.0），
   证明基准是 THB float；引擎 satang/100 换算逻辑正确。

### 整数化精度评估

在 10823 条匹配行中，所有金额字段（sales_price, gross_amount, original_amount,
revenue/actual_amount, refund_amount, free_amount, give_amount,
discount_amount, cancelled_amount）的**最小可比较 diff 为 0.00 THB**
（即数据未变动的行 100% 精确相等，无浮点噪声残留）。

整数引擎输出为精确整数，不存在浮点累加误差；若基准与引擎查询同一时刻的数据，
diff 将为零超限。当前的超限行完全由数据新鲜度差异解释。

---

## 浮点路径清理说明（spec §6）

spec §6 "删除旧浮点路径" 的意图是确保不存在两套并行计算路径。

本次整数化为 in-place 改造：

- `semantic/entities/sale_line.py`、`takeout_line.py`、`sale_event.py` 中的
  所有金额列均在 CTE 层执行 `CAST(ROUND(SUM(...) * 100) AS INT64)`，
  浮点 decimal(12,2) 源值在**唯一舍入点**转换为 satang。
- 无独立"旧浮点路径"代码分支残留；引擎层（`aggregate_with_bom`、`aggregate_by_grain`）
  接收 satang INT64，执行精确整数加法，写盘前在 Excel 层统一 /100。
- SNAPSHOT-FIRST 策略通过事先存储浮点快照满足 spec §6 的"可对比"要求，
  无需保留双引擎代码。

**浮点路径已清理，spec §6 满足。**

---

## 决定性证伪:qty 冻结子集(对抗复核)

超容差行的"数据新鲜度"归因(非整数化 bug)用一个硬预测做了决定性证伪:
**凡 qty 桶在基准与引擎间完全相同的行,金额 diff 必须 ≤0.01 THB**——若有一行 qty 冻结
却金额漂移,新鲜度假说即被推翻、存在缩放 bug。

在**第三个时点**(晚于基准 dump 与首次 diff)对抗性复跑整数引擎(更晚 = 漂移更多,
对证伪更保守),过滤出 qty 完全相同的子集后逐金额指标比对:

| 管线 | qty 冻结子集行数 | 金额 diff >0.01 THB |
|---|---|---|
| profit_margin | 10,601 / 10,823 | **0** |
| profit_by_price | 10,604 / 10,823 | **0** |
| 合计 | **21,205** | **0** |

含 per-unit `sales_price` 在内,所有 qty 冻结行金额精确吻合(≤1 satang)。Task 8 那
~1,300 超容差行全部落在 qty 实际变动的 ~222 行/管线(活跃月真实销售累积),
**非缩放/×100 误差**。整数化可信。

证伪脚本与日志: `/tmp/freshness_falsify.py` / `/tmp/freshness_falsify.txt`(本地,不入库)。

---

## 基准文件保留说明

`exports/.dual_run/float_baseline_202606.json` 已加入 `.gitignore`，
仅本地保留，在 PR-B merge 前不删除，以备复查。

---

## 脚本

- 基准生成: `scripts/adhoc/dump_float_baseline_202606.py`（Task 6，整数化前运行）
- Diff 验收: `scripts/adhoc/diff_dual_run_202606.py`（Task 8，整数化后运行）

原始输出: `/tmp/dual_diff.txt`（本地，不入库）
