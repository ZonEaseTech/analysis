# CLAUDE.md — 华莱士 BigQuery 分析工作区

## 必须遵守的规则

### 0. 接 adhoc 导表任务 / 客户给新事实表 → 必走 `adhoc-export` skill

⚠️ 任何下面这类任务,**禁止直接动手, 必须先走 `.claude/skills/adhoc-export/SKILL.md`**:

- 市场/老板说"要个表 / 临时导一份 / 帮我看看 XX 数据"
- 客户/同事甩新 Excel/CSV(BOM / 物料价 / 商家 / 销售对账单)过来
- "重新导一次 X 月报表"
- 改 `resources/config.yaml` 任何 priority 栈
- 改 `bq_reports/*.py` 报表脚本

**唯一活配置 = `resources/config.yaml`**(报表脚本默认读这份)。
新数据文件按 `resources/wallace.<日期>/` 归档,但 config 永远只改 `resources/config.yaml`,
不要再往 `resources/wallace.*/` 里放 config.yaml。历史版本靠 git history 回溯。

skill 强制 4 步审现状(查当前 config / 看 ground truth / 对照 / 确认报表类型), 跳过任一步就会踩历史踩过的坑 — 用错 config / 用错报表 / 用错事实表 / 误信 audit. 不要凭直觉.

### 0.5. 出利润/成本 → 套餐必须"订单→商品→物料"逐层拆,禁止摊销,禁止猜

⚠️ 算任何利润/成本(profit_by_price / profit_margin / 成本毛利)时,套餐(combo, product_type=1)成本**只准这一条路**:

```
订单 → 该单实际选中的子品(商品) → 每个子品的定义配方(BOM) → 物料 → × 单价
```

- **单品**(product_type=0):定义配方 × 销量,直接取,整数。
- **套餐**:用订单事实表拿"每单实际选了哪些子品"(堂食 `ttpos_sale_order_product` product_type=2 子品行 / 外卖 `ttpos_takeout_order_item` + `ttpos_takeout_order_material`),再展开子品的定义配方。**按真实选择,不按概率。**
- **两渠道都卖的套餐**:堂食(订单子品×配方)+ 外卖(真实扣料)**按各自销量加权混合**,不准只取一个渠道。
- 口径 = **标准配方成本**(按 ttpos 官方配方该消耗多少 × 销量)。配方真源 = `ttpos_product_bom`/`ttpos_related_material`(验过 = ttpos BOM 卡导出,如薯条 150g 一字不差)。

**死规矩(踩了 v6~v15 十几版的坑总结):**
- ❌ **禁止任何摊销/均匀分配**:`weight = optional_count/candidate_count` 这种期望成本法**绝对不准用** —— 它把可选套餐摊成 1.333 个打包袋这种反常识分数,是历史最大的坑。
- ❌ **禁止猜**:套餐没有"固定配方"就老老实实从订单拆;ttpos 套餐是可选结构(选1/N),配方靠订单真实选择还原,不许拍脑袋摊。
- ⚠️ **小数 ≠ bug**:单份成本是"总料÷份数"的真实平均,出小数(0.75=4单3根吸管、有人不点饮料)是数学必然,**不是摊销、不是错**。要整数只能给固定配方表。
- ⚠️ **两本账别混**:"标准配方成本"(配方×销量)vs"实际扣料"(`*_order_material` 真实扣减)是两个口径,差异正常(油反复用/损耗/部分订单没扣记录),别拿一个去否定另一个。利润表默认用**标准配方成本**。
- 实现真源:`semantic/entities/recursive_bom.py`(订单拆分 + 渠道加权,**已删所有摊销代码**)。改这块前先读它,别再加回 weight。

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

> ⚠️ 工作区**并存两套管线**，别混淆（全景图 + 指标谱系见 `docs/pipelines-overview.md`）：
> - **① semantic / bq_reports** —— live 读 BQ + 多层 priority 解析 + 校验对账，平台方向。
> - **② bom_pipeline** —— 离线读 `clean_bom.csv` 单源、payment 锚定实收，**当前《商品成本毛利分析》生产口径**（文档 `docs/bom-pipeline.md`）。

```
analysis/
├── venv/                              # Python 虚拟环境（必须使用）
├── bq_reports/                        # ① 平台报表入口（live BQ）
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
├── bom_pipeline/                      # ② 同事脚本生产线（成本毛利分析生产口径）
│   ├── wallace_bom_margin.py          # 离线读 clean_bom.csv 算成本毛利（payment 锚定实收，四 sheet）
│   ├── bom_rules.py                   # 渠道/物料删除规则（口径收口）
│   └── erpnext_price.py               # 复刻 ttpos 后端 calculateFinalItemUnitCost（物料采购价）
├── utils/                             # 通用工具（跨业务）
│   ├── cache.py                       # 文件缓存层
│   ├── resource_adapter.py            # 外部资源适配器
│   └── report_engine.py               # 报表引擎（并发查询 + 配置驱动 Excel）
├── resources/
│   ├── config.yaml                    # 唯一活配置（bom_sources priority 栈、门店名）
│   ├── wallace.20260626/              # ② 当月数据（按 wallace.<YYYYMMDD>/ 归档）
│   │   └── clean_bom.csv              # ② BOM 单源（仅 *.csv 入库，xlsx/json 本地保留）
│   └── reports/
│       ├── profit_margin.yaml         # 中间表列定义
│       └── profit_by_price.yaml       # 客户交付物列定义
├── tests/                             # 单元 + 端到端测试（400+）
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
