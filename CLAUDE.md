# CLAUDE.md — 华莱士 BigQuery 分析工作区

## 必须遵守的规则

### 1. 虚拟环境

**所有 Python 脚本必须通过虚拟环境运行。**

```bash
# 正确
venv/bin/python scripts/report_bom_sales_bq.py
venv/bin/python -m bq_reports.profit_margin_report --month 2026-03 --output exports/test.xlsx

# 错误（不要直接用系统 python3）
python3 scripts/report_bom_sales_bq.py
```

依赖安装在 `venv/` 中，包含 `google-cloud-bigquery`、`openpyxl`、`pyyaml`、`requests`、`pycryptodome` 等。

### 2. 项目结构（最新）

```
analysis/
├── venv/                              # Python 虚拟环境（必须使用）
├── bq_reports/
│   ├── profit_margin_report.py        # 中间表（对账锚，BOM 物料展开 32 列）
│   ├── profit_by_price_report.py      # 客户交付物（按价展开 18 列）
│   └── utils/
│       ├── bq_client.py               # BQ 客户端 + 代理
│       └── erpnext_api.py             # ERPNext 价格查询
├── semantic/                          # 业务语义层（口径真源）
│   ├── entities/                      # SQL CTE 工厂（业务实体）
│   │   ├── sale_event.py              # 最细粒度事实表 (item, price, channel)
│   │   ├── sale_line.py               # 旧粒度 (item) — profit_margin 用
│   │   ├── takeout_line.py            # 旧粒度 (item) — profit_margin 用
│   │   ├── total_line.py              # FULL OUTER JOIN 复合
│   │   ├── price_breakdown.py         # top3 价格档拆分
│   │   ├── bom.py / combo.py          # 产品 BOM、套餐结构
│   ├── aggregations/
│   │   └── by_grain.py                # aggregate_by_grain(rows, grain, metrics)
│   ├── dimensions/
│   │   └── time.py                    # BKK_TZ + month_to_ts_range
│   └── validators/                    # 会计恒等式校验器（导出必跑）
│       ├── core.py                    # Identity / Severity / check / print_result
│       └── identities.py              # 销量恒等式、金额恒等式 + 三级阈值
├── utils/                             # 通用工具（跨业务）
│   ├── cache.py                       # 文件缓存层
│   ├── resource_adapter.py            # 外部资源适配器
│   └── report_engine.py               # 报表引擎（并发查询 + 配置驱动 Excel）
├── resources/
│   ├── wallace.20260422/
│   │   ├── config.yaml                # 资源映射配置（门店名、fallback BOM）
│   │   └── 物品消耗计算结果_*.xlsx
│   └── reports/
│       ├── profit_margin.yaml         # 中间表列定义
│       └── profit_by_price.yaml       # 客户交付物列定义
├── tests/                             # 单元 + 端到端测试（>150）
├── exports/                           # 报表输出目录
└── .cache/bq_reports/                 # 缓存文件（自动创建）
```

**新报表的"成本"**:
- 维度已在 `sale_event` 字段里 → 新 yaml + 复制 200 行报表脚本，约 20 分钟
- 维度还不在 → 给 `sale_event` 加一列 SQL + 上述，约 1 小时
- **校验器自动复用，零额外代码**

### 3. 导出必须接校验器（无例外）

**任何把数据写到 Excel/CSV 的报表脚本，导出阶段必须跑 `semantic/validators/`，
console 至少打印 ✅/🟡/🔴 三级摘要。** 这是为了：

- **可审计**：数据出问题时一眼看出"是源 / 聚合 / 落盘哪一层错了"
- **对账闭环**：中间表 (`profit_margin`) 跟交付物 (`profit_by_price` 等) 共享同一份
  identities，任一边违反恒等式立即在 console 里冒出来
- **拒绝胡编乱造**：客户/老板拿到的每个数字背后都有数学保证，不是"看起来差不多"

#### 最小集成（4 行 + 一个字典构造）

```python
from semantic.validators import check, print_result
from semantic.validators.identities import DEFAULT_IDENTITIES

check_rows = [{"store_num": ..., "item_name": ..., **agg_metrics} for ...]
result = check(check_rows, DEFAULT_IDENTITIES)
print_result(result, row_label=lambda r: f"店 {r['store_num']}  {r['item_name']}")
if result.has_must_fix:
    print("⚠️  有 🔴 离谱违反，请核实数据/口径。")
```

`check_rows` 里 row 必须包含 identities 用到的字段：
`qty`, `net_qty`, `sales_price`, `revenue`, `free_qty`, `give_qty`,
`refund_qty`, `refund_amount`, `free_amount`, `give_amount`,
`discount_amount`, `cancelled_qty`, `cancelled_amount`。

新报表如果有新维度/新指标，**新加 identity** 到 `semantic/validators/identities.py`，
不要写"特殊容忍"在报表脚本里——所有口径只在 identities 文件里收口。

#### 现成可复用的报表模式

```python
from utils.report_engine import ReportEngine, load_sheet_config

# 1. SQL 模板  →  优先从 semantic/entities/ 拼装，不要现写 GROUP BY
SQL = "WITH ... FROM `{project}`.`{dataset}`.`ttpos_xxx` ..."

# 2. 聚合逻辑  →  优先用 semantic/aggregations/aggregate_by_grain
from semantic.aggregations.by_grain import aggregate_by_grain
grouped = aggregate_by_grain(raw_rows, GRAIN_KEYS, METRIC_KEYS)

# 3. 列配置（YAML）
# resources/reports/my_report.yaml

# 4. 校验器（必跑，见上方）
```

引擎封装了：代理设置、BQ 连接、并发查询、外部资源加载、Excel 样式、合并单元格、公式、条件格式、缓存。

### 4. 外部资源适配规则

客户提供的 Excel/CSV 格式变化时：
- **不修改 Python 代码**
- 修改 `resources/` 下的 YAML 配置
- 支持：列回退、多 sheet 读取、回退链、条件路由

### 5. 代理设置

默认不设代理（当前机器在海外，直连 BigQuery / ERPNext）。
如需代理，设置 `BQ_PROXY` 环境变量：

```bash
BQ_PROXY=http://127.0.0.1:7897 venv/bin/python -m bq_reports.profit_margin_report ...
```

`bq_client.setup_proxy()` 和各报表脚本里的同名函数都会读 `BQ_PROXY`，未设置时为 no-op。
