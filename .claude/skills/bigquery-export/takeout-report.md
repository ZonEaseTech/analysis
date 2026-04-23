# 外卖营业额统计

> 华莱士标准口径。判定外卖订单后按 `sale_bill` 聚合营业额 / 实收。

---

## 统计口径

外卖订单判定（满足**任一条件**即计入，自动去重）：

| 条件 | 判定逻辑 | 数据源 |
|------|----------|--------|
| **条件1** | 支付方式匹配 `Robinhood/Grab/Lineman/Shopee`（不区分大小写/空格） | `ttpos_payment_order` |
| **条件2** | 订单来源渠道匹配 `Grab/LINE MAN`（多语言名称或 JSON 快照） | `ttpos_order_source` |
| **条件3** | 账单类型为会员外送 `bill_type = 2` | `ttpos_sale_bill` |
| **外加** | 外卖平台已完成订单 `platform IN ('grab', 'lineman', 'shopee')` | `ttpos_takeout_order` |

金额口径：
- **外卖营业额**：`SUM(sale_bill.amount)` 中满足外卖条件的账单
- **外卖实收**：`SUM(sale_bill.payment_amount)` 同上

---

## 工具类

```python
from utils.takeout_detector import TakeoutOrderDetector

# 使用默认配置
detector = TakeoutOrderDetector.default()

# 生成 SQL 条件片段
sql_condition = detector.build_payment_condition("po")
# 结果: REGEXP_CONTAINS(LOWER(REPLACE(po.payment_method_name, ' ', '')), r'robinhood|grab|lineman|shopee')

# 获取完整 SQL 模板
sql_template = detector.get_sql_template()
```

---

## SQL 模板

业务规则（同一 `sale_bill` 只计一次，满足任一即外卖单）：

1. **支付方式**：该账单下任一 `sale_order` 的 `payment_order`（`related_type=0`、`status=1`）中，`payment_method_name` 去空格、小写后匹配 `robinhood|grab|lineman|shopee`。
2. **Grab / LINE MAN 渠道**：`order_source_uuid > 0`，且 `order_source` 多语言名或 `sale_bill.order_source_name` JSON 快照去空格、小写后含 `grab` 或 `lineman`。
3. **外卖/外送/打包**：`bill_type = 2`（会员外送）或 `order_source_uuid > 0` 或 `dining_method = 1`（打包）。

> 与现有「总营业额」对齐时请在 SQL 中按需改为 `origin_amount`。

仓库内现成文件：
- `ttpos-scripts/bigquery/takeout_revenue_query.sql` — 单 dataset 模板
- `ttpos-scripts/bigquery/export_takeout_revenue.py` — 多店 CSV + 可选合并 Excel Sheet1

---

## 交叉校验机制

导出报表**必须**执行以下校验：

```python
from utils.validators import (
    ValidationChain,
    ConsistencyValidator,
    RangeValidator,
    RatioValidator,
    CrossSourceValidator
)

# 创建校验链
validators = ValidationChain()

# 1. 内部一致性校验
validators.add(ConsistencyValidator(
    total_field='total_turnover',
    sum_fields=['takeout_turnover', 'non_takeout_turnover']
))

# 2. 数值范围校验
validators.add(RangeValidator([
    {"field": "total_turnover", "min": 0, "name": "总营业额非负"},
    {"field": "takeout_turnover", "min": 0, "name": "外卖营业额非负"},
]))

# 3. 比例校验（外卖占比不超过100%）
validators.add(RatioValidator(
    parent_field='total_turnover',
    child_field='takeout_turnover',
    max_ratio=1.0
))

# 4. 跨源比对校验（抽样）
validators.add(CrossSourceValidator(
    bq_client=client,
    sample_count=5,
    compare_field='total_turnover'
))

# 执行校验
result = validators.validate(excel_data)
if not result.is_valid:
    print("校验失败:", result.errors)
```

### 快捷函数

```python
from utils.validators import create_default_validators

# 一键创建外卖营业额报表的标准校验链
validators = create_default_validators(
    total_field="total_turnover",
    takeout_field="takeout_turnover",
    non_takeout_field="non_takeout_turnover"
)
```
