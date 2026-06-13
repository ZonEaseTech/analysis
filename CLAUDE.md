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

**任何把数据写到 Excel/CSV 的报表脚本，导出阶段必须跑 `semantic/validators/`。**
这是为了：

- **可审计**：数据出问题时一眼看出"是源 / 聚合 / 落盘哪一层错了"
- **对账闭环**：中间表 (`profit_margin`) 跟交付物 (`profit_by_price` 等) 共享同一份
  identities，任一边违反恒等式立即在 console 里冒出来
- **拒绝胡编乱造**：客户/老板拿到的每个数字背后都有数学保证，不是"看起来差不多"

#### 最小集成（闸门语义，非建议性）

```python
from semantic.validators.gate import validate_and_gate, add_watermark_sheet_xlsxwriter
from semantic.validators.identities import FULL_IDENTITIES

outcome = validate_and_gate(check_rows, FULL_IDENTITIES,
                            force=args.force, report_name="my_report",
                            row_label=lambda r: f"店 {r['store_num']}")
# 有 🔴 且无 --force → 已在函数内 exit 2, 不产出文件
if outcome.needs_watermark:   # --force 强制导出
    add_watermark_sheet_xlsxwriter(wb, outcome.watermark_lines())
```

`check_rows` 里 row 必须包含 identities 用到的字段：
`qty`, `net_qty`, `sales_price`, `revenue`, `free_qty`, `give_qty`,
`refund_qty`, `refund_amount`, `free_amount`, `give_amount`,
`discount_amount`, `cancelled_qty`, `cancelled_amount`。

新报表如果有新维度/新指标，**新加 identity** 到 `semantic/validators/identities.py`，
不要写"特殊容忍"在报表脚本里——所有口径只在 identities 文件里收口。

**"无例外"是机制不是口号**:`tests/test_validator_coverage.py` AST 扫描
`bq_reports/*.py`,不接闸门(直调 `validate_and_gate` 或经 `GateSpec` 走
bq_exporter 集中式钩子)的脚本直接挂测试。
校验失败默认 exit 2 不产出文件;`--force` 强制导出的文件首页带红色水印
"⚠️校验未通过",不得对外交付。
非销售类导出(BOM/菜单)用 `make_required_fields_identity` /
`make_unique_key_identity` 基线,不许裸奔;空表语义按 sheet 判定
(`min_rows=0` 仅用于"空=好状态/空=合法"的诊断类数据集,代码内注释说明)。

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

### 5.5 历史封存(零容差口径切换线)

**封存线 = 2026-06**。2026-05 及之前的月份为旧浮点口径交付物,**永不重算**
(`semantic/dimensions/time.py: assert_month_not_frozen`,报表入口拒跑 exit 3)。
新旧口径数字不可逐分比较;只读对账/审计查询不受限。
依据:spec 决策 2/3(`docs/superpowers/specs/2026-06-12-zero-tolerance-design.md`)。

### 6. 技术债清单(零容差改造)

spec: `docs/superpowers/specs/2026-06-12-zero-tolerance-design.md`
基线: `docs/audit/2026-06-cross-ledger-baseline.md`

| # | 债 | 还债条件 |
|---|---|---|
| ① | ~~2026-05 前旧口径封存待机制化~~ | 已落地: month guard (semantic/dimensions/time.py), 2026-06-13 |
| ② | 支付勾稽 (bill.payment_amount vs 统计账实收) 封顶 🟡, 实测全店差 30-50% | service_fee/tax_fee/整单折扣/会员储值口径校准后转红线 |
| ③ | 外卖平台侧退款不在恒等式内 | 对账桥范围, 子项目 D 接平台对账单后 |
| ④ | sale_event / sale_line 双轨并存; profit_margin/sales_period 的 gross_amount 是定义式补齐 | sale_line/takeout_line 投影 gross_amount (PR-B), CROSS_LEDGER 证明等价后合并双轨 |
| ⑤ | pnl_statement 只接非空闸门, 销售恒等式待 P&L 行字段对齐 | PR-B 整数化时一并对齐 |
| ⑥ | CROSS_LEDGER 未进闸门: 凭证账含套餐子项行, 粒度不齐 (qty match 31.5%) | PR-B 修 order_line 套餐口径 → 复跑观察 → 100% 后进闸门 |
| ⑦ | 外卖勾稽 2 单偏差未归因 (shop006/373316429388, shop059/372817075896) | PR-B 观察复跑时一并排查 |
