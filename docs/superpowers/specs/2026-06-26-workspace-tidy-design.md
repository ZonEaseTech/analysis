# 工作区代码整理设计

> 目标: **数据架构优先 + 可维护性**。把外围游离物归位、消除模糊边界、让顶层一眼看清"数据核心 + 报表消费层"的骨架。

## 最终结构

```
analysis/
├── semantic/                     # 🔵 数据架构核心 (不改内部)
│   ├── entities/                 # CTE 工厂 (12 实体)
│   ├── cogs/                     # 成本解析 (material_price 4层)
│   ├── aggregations/             # 聚合层
│   ├── metrics/                  # 指标注册表 (5 yaml, 30 指标)
│   ├── validators/               # 恒等式 + 闸门
│   ├── reconciliation/           # 对账锚 (4 checks)
│   ├── resolvers/                # priority 解析器 + layered_resource ←NEW
│   ├── dimensions/               # 时间/业务维度
│   ├── analytics/                # 归因分析
│   ├── comparison/               # 期间对比
│   └── __init__.py
│
├── bq_reports/                   # 🟢 报表层 = 数据排列组合
│   ├── shared/                   # NEW: report_engine + cache (报表引擎基础设施)
│   ├── utils/                    # bq_client / erpnext_api (BQ+ERP客户端, 不动)
│   ├── [19 个报表入口 .py]       # 不变
│   └── __init__.py
│
├── bom_pipeline/                 # 🟡 生产管线 (不动)
│   ├── wallace_bom_margin.py
│   ├── erpnext_price.py
│   ├── bom_rules.py
│   └── __init__.py
│
├── external_sales/               # 🟠 外部销售 (被 bq_exporter 使用, 不动)
│
├── utils/                        # ⚪ 精瘦: 只留 resource_adapter (真跨层共享)
│   ├── __init__.py
│   └── resource_adapter.py
│
├── resources/                    # 配置 + wallace.{date}/ 归档 (不动)
├── tests/                        # 584 tests (不动)
├── docs/                         # 文档 (不动)
├── exports/                      # 导出物 (gitignored, 不动)
│
├── scripts/
│   ├── adhoc/                    # 现役审计/对账/接入脚本 (~22)
│   │   └── _archive/             # 历史归档 (~27)
│   └── run.sh                    # 修复后
│
├── CLAUDE.md                     # 更新结构段
├── README.md                     # 更新
└── requirements.txt
```

## 逐项改动

### 1. 删 (3 项)

| 目标 | 现状 | 理由 |
|---|---|---|
| `engine/` | 1 个 `__init__.py`, 仅 docstring | YAGNI, 0 引用 |
| `sources/` | 1 个 `__init__.py`, 仅 docstring | YAGNI, 0 引用 |
| `statistics_bq_draft.go` | 333 行 Go, 顶层, 0 引用 | 移到 `docs/reference/` 存档 |

### 2. 搬 (3 文件, 不删 utils/)

实际消费关系(已核实):

| 文件 | 消费者 | 归属 |
|---|---|---|
| `utils/report_engine.py` | bq_reports/ 3 报表 + scripts/tests | → `bq_reports/shared/` |
| `utils/cache.py` | report_engine + profit_margin | → `bq_reports/shared/` |
| `utils/layered_resource.py` | semantic/ + bq_reports/ | → `semantic/resolvers/` |
| `utils/resource_adapter.py` | semantic/ + reconciliation/ + bq_reports/ | 真跨层共享 → 留在 `utils/` |

```
utils/report_engine.py    → bq_reports/shared/report_engine.py
utils/cache.py            → bq_reports/shared/cache.py
utils/layered_resource.py → semantic/resolvers/layered_resource.py
utils/resource_adapter.py → 留在原位 (utils/)
```

`utils/` 从 4 文件瘦到 1 文件(`resource_adapter.py` + `__init__.py`)。所有仓库内 import 路径同步更新。`semantic/` 不再反向依赖 `bq_reports/`（`material_price.py` 和 `resolvers/loader.py` 引用 `resource_adapter`——它还在 `utils/`,路径不变;引 `layered_resource` 的改成 `from semantic.resolvers.layered_resource`）。

### 3. scripts/ 归位

**进 `scripts/adhoc/` (现役, ~22 个):**

| 类别 | 脚本 | 理由 |
|---|---|---|
| 审计对账 | `audit_*.py` (10个) + `README.md` | 跨账本/外卖/费率/隐藏订单审计, 还在用 |
| 对账桥 | `recon_cost_vs_summary_bridge.py` | 成本表 vs 汇总表桥, 已对齐 |
| 差分对比 | `diff_*.py` (2) | 双跑对比/成本规则 diff |
| 接入脚本 | `onboard_*.py` (2) | BOM/平台费接入, 新月份复用 |
| 校验 | `validate_report.py` | report 语义校验 |
| 生产工具 | `business_summary.py` | 对齐 ttpos 62/62, 市场自助导出 |
| 基线/排障 | `dump_float_baseline_202606.py`, `scan_suspicious_2026_02.py`, `discover_schema.py`, `report_lineman_burger_bogo.py`, `compare_outputs.py` | 单次产出但有参考价值 |

**进 `scripts/adhoc/_archive/` (历史, ~27 个):**

| 类别 | 脚本 |
|---|---|
| Grab 支付 (已完成, 6) | `check_grab_payment.py`, `_v2.py`, `export_grab_payment.py`, `match_grab_statement.py`, `reconcile_grab.py`, `trace_order_by_payment.py` |
| BOM 清洗 (已取代, 4) | `parse_colleague_bom.py`, `parse_merged_bom.py`, `clean_colleague_combo_bom.py`, `clean_market_bom_202603.py` |
| Debug (排查完, 2) | `debug_erpnext_auth.py`, `debug_erpnext_uom.py` |
| 废弃/历史排查 (13) | `serve_dashboard.py`, `reconcile_product_names.py`, `reconcile_statement.py`, `list_april_materials.py`, `investigate_price_change.sql`, `drop_listprice_receivable.py`, `batch_reconcile.py`, `verify_deleted_combo.py`, `verify_deleted_single.py`, `audit_report.py`, `sales_price_sql.py`, `calc_sales_price.sql`, `validate_v2.py` |
| 顶层移动后清空 | adhoc/ 原 _archive/ 已有 11 个历史文件, 不动 |

### 4. 修

| 目标 | 修法 |
|---|---|
| `run.sh` | ① 删失效的 `ls *.py` 列表 ② `python3` → `venv/bin/python` ③ 帮助信息指向 `scripts/adhoc/` 和 `bq_reports/` |
| `CLAUDE.md` | ① 更新 §2 项目结构段 ② `bq_reports/shared/` = 报表引擎基础设施 ③ `utils/` = 跨层共享适配器(resource_adapter) |
| `README.md` | 同步顶层目录说明 |

### 5. 不改 (红线)

- `semantic/` 内部子目录结构 (只加 `layered_resource.py` 到 `resolvers/`, 不改已有文件)
- `bq_reports/` 19 个报表脚本的**内部逻辑** (只更新 import 路径)
- `bom_pipeline/` — 独立生产管线
- `external_sales/` — 真实被引用 (`bq_exporter.py:952`)
- `tests/` — 584 测试结构不动

## 验证清单

- [ ] 584 tests 全绿
- [ ] `venv/bin/python -c "from bq_reports.shared import report_engine"` 成功
- [ ] `venv/bin/python -c "from bq_reports.shared.cache import cached"` 成功
- [ ] `venv/bin/python -c "from semantic.resolvers.layered_resource import LayeredResource"` 成功
- [ ] `./run.sh --help` 正常
- [ ] `grep -r "from utils.cache\|from utils.report_engine\|from utils.layered_resource" --include='*.py' . | grep -v venv` → 零结果
- [ ] `grep -r "from engine\|import engine" --include='*.py' . | grep -v venv` → 零结果
- [ ] `grep -r "from sources\|import sources" --include='*.py' . | grep -v venv` → 零结果
- [ ] `grep -r "from utils import resource_adapter\|from utils.resource_adapter" semantic/ --include='*.py'` → 仍有结果(utils/ 保留了 resource_adapter, semantic 引用它不应该变)
- [ ] `git status --short` 无意外文件
