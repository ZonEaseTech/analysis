# 利润报表（成本 + 售价 + 毛利率）

> 基于 BOM 结构计算单品/套餐成本，结合销售额算毛利和毛利率。

---

## 成本计算口径

```
单份总成本 = Σ(BOM消耗数量 × 物料单价)
毛利       = 销售额 − 单份总成本 × 销量
毛利率     = 毛利 / 销售额
```

**注意**：BOM `num` 已经是目标单位消耗量，**不除** `conversion_rate`。

---

## 数据源分层

| 层级 | 数据源 | 用途 | 优先级 |
|------|--------|------|--------|
| 主源 | BQ `ttpos_product_bom` + `related_material` | BOM 结构 + 消耗量 | 第一 |
| 定价 | ERPNext `Item Price` | 物料单价（按 UOM 优先级） | 第一 |
| Fallback | 客户提供的 Excel/表格 | BOM 补充（当 BQ BOM 为空壳时） | 第二 |
| 映射 | 客户提供的门店列表 Excel | 门店编号 → 名称 | 参考 |

---

## 套餐去重规则

`ttpos_sale_order_product` 中套餐 parent（`product_type=1`）和 child（`product_type=2`）同时存在时：
- **只保留 parent 行**聚合销量和销售额
- child 行的 `parent_sop_uuid` 指向 parent，用于识别归属
- 避免同一订单中套餐物料被重复计算

---

## Excel 公式模式

把计算列交给 Excel 公式，方便客户调价格后自动重算：

```python
# L: 物品总成本 = 单价 × 消耗数量 × 销量
ws.cell(row=r, column=12, value=f"=I{r}*J{r}*D{r}")

# M: 单份总成本 = SUMPRODUCT(单价列, 消耗列)
ws.cell(row=start, column=13, value=f"=SUMPRODUCT(I{start}:I{end},J{start}:J{end})")

# N: 毛利 = 销售额 − 单份总成本×销量
ws.cell(row=start, column=14, value=f"=F{start}-M{start}*D{start}")

# O: 毛利率 = IF(销售额=0, 0, 毛利/销售额)
ws.cell(row=start, column=15, value=f"=IF(F{start}=0,0,N{start}/F{start})")
ws.cell(row=start, column=15).number_format = "0.00%"
```

**限制**：几万行带公式的 Excel 打开会很慢。静态值 + 公式混合策略：A-K 写值，L-O 写公式，合并单元格区域只在左上角写公式。

---

## 多进程加速写入

```python
from multiprocessing import Process

def write_sheet(sheet_name, rows, headers, output_path, sheet_index):
    wb = load_workbook(output_path)
    ws = wb.create_sheet(title=sheet_name, index=sheet_index)
    # ... 写入逻辑
    wb.save(output_path)

# 每个 Sheet 一个进程
processes = [
    Process(target=write_sheet, args=("套餐", combo_rows, headers, output_path, 0)),
    Process(target=write_sheet, args=("单品", single_rows, headers, output_path, 1)),
]
for p in processes:
    p.start()
for p in processes:
    p.join()
```

---

## SQL 性能优化：避免 JOIN 膨胀

当 SQL 涉及 订单 → 子产品 → BOM → 物料 多级 JOIN 时，结果行数会指数级增长。

**问题示例**：
- 1 个套餐订单含 3 个子产品，每个子产品含 2 个 BOM 物料
- JOIN 后 1 条订单 → 6 条结果行（6x 膨胀）
- 53 店 × 日均 1000 单 × 6 = 318,000 行/天 传输到 Python

**优化方案：三查询分离**

```
查询1（订单聚合）
  → 在 BQ 内 GROUP BY product_uuid，返回 (uuid, name, SUM(qty), SUM(revenue))
  → 每店仅 ~30-80 行

查询2（BOM 结构）
  → 从产品表 ttpos_product_bom + related_material 查
  → 不扫描订单表，每店 ~500-800 行
  → 缓存 TTL：1天

查询3（套餐结构，仅套餐模式）
  → 从订单查 combo_uuid → child_uuid 的 DISTINCT 映射
  → 结果有界（稳定菜单下每店 ~100-400 行）
  → 缓存 TTL：7天

Python 侧合并
  → 按 product_uuid 将订单与 BOM 匹配
  → 套餐通过 structure 找到 child_uuids，汇总 child BOM
```

**效果对比**（53 店利润报表）

| 指标 | 旧版（单查询 JOIN） | 新版（三查询分离） |
|------|---------------------|---------------------|
| 传输数据量 | ~200 万行 | ~5 万行 |
| 单日导出耗时 | 3-7 分钟 | ~1 分钟 |
| BQ 扫描费用 | 高（JOIN 多级表） | 低（订单表仅扫一次） |
| 单品结果 | — | 与旧版完全一致 |
| 套餐结果（单日） | — | 与旧版完全一致 |
| 套餐结果（多日） | — | 差异 <0.1% |

**差异原因与取舍**：
- 套餐结构从"时间范围内所有订单"推断，若菜单在期间变化，新版会汇总所有出现过的子产品的 BOM
- 旧版按每个订单实时关联当时的子产品和 BOM，更精确但慢
- **单日查询：结果完全一致**；**多日查询：差异 <0.1%，可接受**
- 若需绝对精确且能容忍 3-7 分钟耗时，保留旧版 JOIN 方式作为 `--precise` fallback

**BOM 从产品表 vs 从订单关联的区别**：
旧版通过 `sale_order_product_bom` 关联订单到具体的 `product_bom_uuid`，同一产品在不同订单中可能使用不同 BOM 记录。新版直接从 `ttpos_product_bom` 查，若同一 `product_package_uuid` 存在多条 BOM 记录，会全部返回。实测中 94% 的产品只有一条 BOM 记录，差异可忽略。

---

## 多源跨店月报参考

利润报表常需结合盘点/调入/消耗等多维度数据。典型模式：

- **跨店循环**：每个维度独立循环 N 个 dataset，用 `defaultdict` 按 `(material_code, material_name)` 聚合
- **多数据源**：BQ 查 TTPOS 数据，ERPNext API 查采购数据，Python 侧 merge
- **基准单位**：盘点用 `counted_quantity`（已是基准单位），调入用 `num * unit_conversion_rate`，消耗用 `som.num`
- **日期参数化**：起止日期通过命令行参数传入，可复用于任意月份

具体代码模板见 [query-patterns.md](query-patterns.md) Pattern F（多源跨店月报）。

---

## 相关陷阱

| 陷阱 | 说明 | 解法 |
|------|------|------|
| BOM 空壳 | `product_bom_card` 存在但 `related_material` 为空，成本计算为 0 | JOIN `related_material` 验证，空 BOM 从外部数据源补充 |
| 套餐重复扣料 | parent + child 都出现在 `sale_order_product`，同一物料被重复计算 | 按 `parent_sop_uuid` 去重，只保留 parent 行 |
| 大量公式 Excel 卡顿 | 几万行带公式打开极慢 | 公式改为静态值；或让客户手动填充 |
| ERPNext 价格 UOM 歧义 | 同一 `item_code` 多个 `Item Price`（g/pc/Nos 等） | 按 UOM 优先级排序（g > pc > Nos > pkt > ctn），同优先级取最新 modified |
| 单位换算误用 | BOM 的 `num` 已是目标单位消耗量 | 成本 = `num × price_per_stock_uom`，不除 conversion_rate |
| 百分比格式丢失 | 公式单元格不显示百分比 | 显式设置 `cell.number_format = "0.00%"` |
| 缓存未分层 | 每次重复查门店名称、BOM 结构等静态数据 | 对静态/准静态数据加 TTL 缓存，按时间范围 key 隔离 |
