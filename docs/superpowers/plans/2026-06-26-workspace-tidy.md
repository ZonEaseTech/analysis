# 工作区代码整理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把外围游离物归位、消除模糊边界、让顶层一眼看清数据核心 + 报表消费层的骨架。纯机械动作(删/搬/改名),不改任何逻辑。

**Architecture:** 按消费关系分家 — `semantic/` 永不 import `bq_reports/`; `report_engine`+`cache` 归报表层; `layered_resource` 归 semantic; `resource_adapter` 独留 utils/ 作跨层共享。scripts/ 按"现役 vs 历史一次"归位到 `adhoc/` 或 `_archive/`。

**Tech Stack:** bash + git mv + sed import 替换 + Python unittest 验证

---

## 文件结构

| 文件 | 动作 | 说明 |
|---|---|---|
| `engine/__init__.py` | 删 | 空壳,0 引用 |
| `sources/__init__.py` | 删 | 空壳,0 引用 |
| `statistics_bq_draft.go` | 移 | → `docs/reference/statistics_bq_draft.go` |
| `utils/cache.py` | 移 | → `bq_reports/shared/cache.py` |
| `utils/report_engine.py` | 移 | → `bq_reports/shared/report_engine.py` |
| `utils/layered_resource.py` | 移 | → `semantic/resolvers/layered_resource.py` |
| `bq_reports/shared/__init__.py` | 新建 | export report_engine + cache |
| `scripts/` 顶层 23 个文件 | 移 | 14 → `adhoc/_archive/`, 4 → `adhoc/`, 5 被顶替不动 |
| `scripts/adhoc/` 5 个文件 | 移进 `_archive/` | validate_v2, clean_colleague_combo, clean_market_bom, debug_erpnext_auth, debug_erpnext_uom |
| `run.sh` | 修 | 指向实际入口 + venv |
| `CLAUDE.md` | 修 | 更新结构段 |
| `README.md` | 修 | 同步顶层目录 |

---

### Task 1: 删除游离空壳 + 存档 go 草稿

**Files:** 删 `engine/` `sources/`; 移 `statistics_bq_draft.go` → `docs/reference/`

- [ ] **Step 1: 确认 engine/sources 是真空壳(无消费者)**

```bash
grep -rn "from engine\|import engine" --include='*.py' . 2>/dev/null | grep -v venv | grep -v __pycache__
grep -rn "from sources\|import sources" --include='*.py' . 2>/dev/null | grep -v venv | grep -v __pycache__
```
Expected: 零结果(确认无引用)

- [ ] **Step 2: 建 reference 目录 + 移 go 草稿**

```bash
mkdir -p docs/reference
git mv statistics_bq_draft.go docs/reference/statistics_bq_draft.go
```

- [ ] **Step 3: 删空壳目录**

```bash
git rm -rf engine/ sources/
```

- [ ] **Step 4: 验证并提交**

```bash
grep -rn "from engine\|import engine\|from sources\|import sources" --include='*.py' . | grep -v venv | grep -v __pycache__
# Expected: 零结果
git add docs/reference/statistics_bq_draft.go
git commit -m "$(cat <<'EOF'
chore: 删空壳包 engine/sources + go 草稿移 docs/reference/

engine/ 和 sources/ 是空壳(YAGNI,0引用)
statistics_bq_draft.go 是333行 Go 草稿,存档为参考

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 搬 report_engine + cache → bq_reports/shared/

**Files:**
- Move: `utils/report_engine.py` → `bq_reports/shared/report_engine.py`
- Move: `utils/cache.py` → `bq_reports/shared/cache.py`
- Create: `bq_reports/shared/__init__.py`
- Modify: 更新所有 import 引用

**Import 更新清单(已核实):**

| 文件 | 旧 import | 新 import |
|---|---|---|
| `utils/report_engine.py:55` | `from utils.cache import get_cache, set_cache, cache_key` | `from bq_reports.shared.cache import get_cache, set_cache, cache_key` |
| `utils/report_engine.py:21` | `from utils.report_engine import ...` (自引用) | `from bq_reports.shared.report_engine import ...` |
| `utils/report_engine.py:56` | `from utils.resource_adapter import get_adapter` | 不变(`resource_adapter` 仍在 `utils/`) |
| `bq_reports/profit_margin_report.py:47` | `from utils.cache import get_cache, set_cache, cache_key` | `from bq_reports.shared.cache import get_cache, set_cache, cache_key` |
| `bq_reports/profit_margin_report.py:48` | `from utils.report_engine import ReportEngine, load_sheet_config` | `from bq_reports.shared.report_engine import ReportEngine, load_sheet_config` |
| `bq_reports/profit_margin_report.py:49` | `from utils.resource_adapter import get_adapter` | 不变 |
| `bq_reports/profit_margin_report.py:107` | `from utils.resource_adapter import get_adapter as _ga` | 不变 |
| `bq_reports/profit_margin_report.py:703` | `from utils.layered_resource import load_layers` | → `from semantic.resolvers.layered_resource import load_layers` (Task 3 处理) |
| `bq_reports/pnl_statement.py:45` | `from utils.report_engine import ReportEngine` | `from bq_reports.shared.report_engine import ReportEngine` |
| `bq_reports/profit_by_price_report.py:57` | `from utils.report_engine import ReportEngine, load_sheet_config` | `from bq_reports.shared.report_engine import ReportEngine, load_sheet_config` |
| `tests/test_erp_price_fallback.py:22` | `from utils import cache as cache_mod` | `from bq_reports.shared import cache as cache_mod` |
| `tests/test_engine_writer.py:21` | `from utils.report_engine import (` | `from bq_reports.shared.report_engine import (` |
| `tests/test_pipeline_smoke.py:27` | `from utils.report_engine import load_sheet_config, write_configured_sheet` | `from bq_reports.shared.report_engine import load_sheet_config, write_configured_sheet` |
| `tests/test_profit_by_price_smoke.py:32` | `from utils.report_engine import load_sheet_config, write_configured_sheet` | `from bq_reports.shared.report_engine import load_sheet_config, write_configured_sheet` |
| `scripts/adhoc/dump_float_baseline_202606.py:62` | `from utils.report_engine import ReportEngine` | `from bq_reports.shared.report_engine import ReportEngine` |
| `scripts/adhoc/diff_dual_run_202606.py:46` | `from utils.report_engine import ReportEngine` | `from bq_reports.shared.report_engine import ReportEngine` |

- [ ] **Step 1: 建 bq_reports/shared/ 目录 + 搬两个文件**

```bash
mkdir -p bq_reports/shared
git mv utils/cache.py bq_reports/shared/cache.py
git mv utils/report_engine.py bq_reports/shared/report_engine.py
```

- [ ] **Step 2: 创建 bq_reports/shared/__init__.py**

```python
"""bq_reports 报表层共享基础设施 — 报表引擎 + 文件缓存。

这些模块原在 utils/(误标为"通用工具"),实际上只被 bq_reports 层消费。
搬到这里后, semantic/ 不再反向依赖 bq_reports/。
"""
from bq_reports.shared.report_engine import ReportEngine, load_sheet_config, ColumnConfig, SheetConfig, query_all_shops
from bq_reports.shared.cache import get_cache, set_cache, cache_key, cached
```

- [ ] **Step 3: 重建 bq_reports/__init__.py (确保 shared 可 import)**

```bash
# 如果 bq_reports/__init__.py 是空的,保留——shared/ 是新子包
```

- [ ] **Step 4: 更新 report_engine.py 内部 import**

`bq_reports/shared/report_engine.py` 内部有三处需要改:

```python
# line 21: 自引用
from bq_reports.shared.report_engine import ReportEngine, ColumnConfig, SheetConfig, query_all_shops

# line 55: cache 引用
from bq_reports.shared.cache import get_cache, set_cache, cache_key

# line 56: resource_adapter 引用(不变,还从 utils/ 读)
from utils.resource_adapter import get_adapter
```

- [ ] **Step 5: 更新所有外部引用文件**

按上表逐文件改,用 sed 或逐文件 Edit。**先改 bq_reports/ 内部,再改 tests/ 和 scripts/。**

重点: `profit_margin_report.py:703` 的 `from utils.layered_resource import load_layers` 也在这个文件里——留到 Task 3 一起修,本任务不改。

- [ ] **Step 6: 验证 import 正确性**

```bash
cd /home/weifashi/hwt/analysis/.dev/worktree/workspace-tidy
PY=/home/weifashi/hwt/analysis/venv/bin/python
$PY -c "from bq_reports.shared.report_engine import ReportEngine; print('report_engine OK')"
$PY -c "from bq_reports.shared.cache import cached; print('cache OK')"
# 确认没有残留的旧 import
grep -rn "from utils.cache\|from utils.report_engine" --include='*.py' . | grep -v venv | grep -v __pycache__
# Expected: 零结果(utils/ 自身在步骤4里已改)
```

- [ ] **Step 7: 跑全量测试确认不挂**

```bash
$PY -m unittest discover -s tests 2>&1 | grep -E "^Ran|^OK|^FAILED"
# Expected: OK
```

- [ ] **Step 8: 提交**

```bash
git add bq_reports/shared/ bq_reports/__init__.py
git add -u  # 所有修改过的文件
git commit -m "$(cat <<'EOF'
refactor: report_engine + cache 搬入 bq_reports/shared/

消费方全在 bq_reports 层,不属于 utils/。semantic/ 不再反向依赖 bq_reports/。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: layered_resource → semantic/resolvers/

**Files:**
- Move: `utils/layered_resource.py` → `semantic/resolvers/layered_resource.py`
- Modify: 更新 `semantic/resolvers/__init__.py` 导出
- Modify: 更新所有引用文件的 import

**Import 更新清单:**

| 文件 | 旧 import | 新 import |
|---|---|---|
| `semantic/resolvers/__init__.py` | `from utils.layered_resource import ...` | `from semantic.resolvers.layered_resource import ...` |
| `semantic/cogs/material_price.py:109` | `from utils.layered_resource import Layer` | `from semantic.resolvers.layered_resource import Layer` |
| `semantic/resolvers/builder.py:22` | `from utils.layered_resource import Layer` | `from semantic.resolvers.layered_resource import Layer` |
| `tests/test_resolvers.py:279` | `from utils.layered_resource import Layer` | `from semantic.resolvers.layered_resource import Layer` |
| `tests/test_resolvers.py:298` | `from utils.layered_resource import Layer` | `from semantic.resolvers.layered_resource import Layer` |
| `tests/test_resolver_parity.py:27` | `from utils.layered_resource import Layer` | `from semantic.resolvers.layered_resource import Layer` |
| `bq_reports/profit_margin_report.py:703` | `from utils.layered_resource import load_layers` | `from semantic.resolvers.layered_resource import load_layers` |

- [ ] **Step 1: 搬文件**

```bash
git mv utils/layered_resource.py semantic/resolvers/layered_resource.py
```

- [ ] **Step 2: 更新 semantic/resolvers/__init__.py**

在现有 export 里加 `layered_resource` 导出(读当前 `__init__.py` 确认导出风格,追加):

```python
from semantic.resolvers.layered_resource import Layer, load_layers
```

- [ ] **Step 3: 逐文件更新 import 语句**

按上表改 7 个文件。

- [ ] **Step 4: 验证**

```bash
cd /home/weifashi/hwt/analysis/.dev/worktree/workspace-tidy
PY=/home/weifashi/hwt/analysis/venv/bin/python
$PY -c "from semantic.resolvers.layered_resource import Layer, load_layers; print('OK')"
grep -rn "from utils.layered_resource" --include='*.py' . | grep -v venv | grep -v __pycache__
# Expected: 零结果
$PY -m unittest discover -s tests 2>&1 | grep -E "^Ran|^OK|^FAILED"
# Expected: OK
```

- [ ] **Step 5: 提交**

```bash
git commit -m "$(cat <<'EOF'
refactor: layered_resource 移入 semantic/resolvers/

它是 resolver priority 栈的基础设施,被 semantic/ 和 bq_reports/ 两边消费。
放在 semantic 域内比 utils/ 更准确(semantic 仍是根,bq_reports 消费它)。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 清理 utils/ 残余 + 恢复 bq_reports/__init__.py

**Files:** 删 `utils/__init__.py`(如果只剩空壳),确保 `utils/` 总目录只剩 `resource_adapter.py` + `__init__.py`

- [ ] **Step 1: 确认 utils/ 现在只有 resource_adapter.py**

```bash
ls utils/
# Expected: __init__.py  resource_adapter.py  (无 cache.py, report_engine.py, layered_resource.py)
```

- [ ] **Step 2: 如果是空的 __init__.py,直接保留,不造新内容**

`resource_adapter.py` 的消费者路径不变: `from utils.resource_adapter import get_adapter`。

- [ ] **Step 3: 验证 + 提交**

```bash
$PY -m unittest discover -s tests 2>&1 | grep -E "^Ran|^OK|^FAILED"
# Expected: OK

git add -A utils/
git commit -m "$(cat <<'EOF'
chore: utils/ 精瘦化完成 — 只留 resource_adapter

cache → bq_reports/shared/ (Task 2)
report_engine → bq_reports/shared/ (Task 2)
layered_resource → semantic/resolvers/ (Task 3)
resource_adapter 独留 (真跨层共享, semantic/ reconciliation/ bq_reports/ 三边消费)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: scripts/ 归位

**Files:** 移动 28 个文件(zero logic changes, pure `git mv`)

**详细 manifest — 进 `scripts/adhoc/` (8 个从顶层):**

```bash
# 这些脚本还在用或有参考价值,从顶层移入 adhoc/
git mv scripts/serve_dashboard.py scripts/adhoc/serve_dashboard.py           # 仪表盘(可能再用)
git mv scripts/verify_deleted_combo.py scripts/adhoc/verify_deleted_combo.py # 删除验证(参考)
git mv scripts/verify_deleted_single.py scripts/adhoc/verify_deleted_single.py
git mv scripts/validate_report_semantics.py scripts/adhoc/validate_report_semantics.py
```

**进 `scripts/adhoc/_archive/` (22 个从顶层 + 5 个从 adhoc/):**

```bash
# 顶层 → _archive (一次性排查/已被取代/版本重复)
git mv scripts/check_grab_payment.py scripts/adhoc/_archive/check_grab_payment.py
git mv scripts/check_grab_payment_v2.py scripts/adhoc/_archive/check_grab_payment_v2.py
git mv scripts/check_grab_payment_cfg.py scripts/adhoc/_archive/check_grab_payment_cfg.py
git mv scripts/export_grab_payment.py scripts/adhoc/_archive/export_grab_payment.py
git mv scripts/match_grab_statement.py scripts/adhoc/_archive/match_grab_statement.py
git mv scripts/reconcile_grab.py scripts/adhoc/_archive/reconcile_grab.py
git mv scripts/trace_order_by_payment.py scripts/adhoc/_archive/trace_order_by_payment.py
git mv scripts/parse_colleague_bom.py scripts/adhoc/_archive/parse_colleague_bom.py
git mv scripts/parse_merged_bom.py scripts/adhoc/_archive/parse_merged_bom.py
git mv scripts/reconcile_product_names.py scripts/adhoc/_archive/reconcile_product_names.py
git mv scripts/reconcile_statement.py scripts/adhoc/_archive/reconcile_statement.py
git mv scripts/list_april_materials.py scripts/adhoc/_archive/list_april_materials.py
git mv scripts/investigate_price_change.sql scripts/adhoc/_archive/investigate_price_change.sql
git mv scripts/calc_sales_price.sql scripts/adhoc/_archive/calc_sales_price.sql
git mv scripts/verify_sales_price.sql scripts/adhoc/_archive/verify_sales_price.sql
git mv scripts/drop_listprice_receivable.py scripts/adhoc/_archive/drop_listprice_receivable.py
git mv scripts/batch_reconcile.py scripts/adhoc/_archive/batch_reconcile.py
git mv scripts/audit_report.py scripts/adhoc/_archive/audit_report.py
git mv scripts/sales_price_sql.py scripts/adhoc/_archive/sales_price_sql.py

# adhoc/ → _archive (排查完/被取代)
git mv scripts/adhoc/validate_v2.py scripts/adhoc/_archive/validate_v2.py
git mv scripts/adhoc/clean_colleague_combo_bom.py scripts/adhoc/_archive/clean_colleague_combo_bom.py
git mv scripts/adhoc/clean_market_bom_202603.py scripts/adhoc/_archive/clean_market_bom_202603.py
git mv scripts/adhoc/debug_erpnext_auth.py scripts/adhoc/_archive/debug_erpnext_auth.py
git mv scripts/adhoc/debug_erpnext_uom.py scripts/adhoc/_archive/debug_erpnext_uom.py
```

- [ ] **Step 1: 逐条执行上面的 git mv**

不需要改任何 Python 代码(scripts 之间互不 import)。

- [ ] **Step 2: 确认 scripts/ 顶层现在空的**

```bash
ls scripts/
# Expected: adhoc/  run.sh
```

- [ ] **Step 3: 验证 + 提交**

```bash
git commit -m "$(cat <<'EOF'
chore: scripts/ 归位 — 现役进 adhoc, 历史进 _archive/

顶层清空,adhoc/ 留现役 ~22, _archive/ 归档历史 ~32。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: 修 run.sh + CLAUDE.md + README.md

**Files:**

- [ ] **Step 1: 修 run.sh**

```bash
#!/bin/bash
# 快速运行分析脚本的快捷方式。
# 用法: ./run.sh <脚本路径> [参数]
#   例: ./run.sh bq_reports/pnl_statement.py 2026-06
#       ./run.sh scripts/adhoc/business_summary.py

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ $# -eq 0 ]; then
    echo "用法: ./run.sh <脚本路径> [参数]"
    echo ""
    echo "常用入口:"
    echo "  bq_reports/profit_margin_report.py    — 中间表(对账锚)"
    echo "  bq_reports/profit_by_price_report.py   — 客户交付物"
    echo "  bq_reports/pnl_statement.py            — 全面 P&L"
    echo "  scripts/adhoc/business_summary.py      — 营业数据汇总"
    echo "  scripts/adhoc/recon_cost_vs_summary_bridge.py — 成本/汇总对账桥"
    exit 1
fi

venv/bin/python "$@"
```

- [ ] **Step 2: 修 CLAUDE.md — 更新 §2 项目结构段**

把当前结构段替换为 spec 里的最终结构,并在顶部加 "报表即数据排列组合" 的原则:

```markdown
### 2. 项目结构(2026-06-26 整理后)

> 原则: 报表 = 不同数据的排列组合。共享的实体/聚合/校验/C端 收口在 `semantic/`。特有的报表逻辑在 `bq_reports/` 内自洽。

analysis/
├── semantic/             # 数据架构核心(口径真源)
│   ├── entities/         # CTE 工厂 (12 实体)
│   ├── cogs/             # 成本解析 (material_price 4层 priority)
│   ├── aggregations/     # 聚合 (by_grain/pnl_layers/kpi_ratios)
│   ├── metrics/          # 指标注册表 (5 yaml, 30 指标, render_catalog)
│   ├── validators/       # 恒等式 + 闸门
│   ├── reconciliation/   # 对账锚 (ttpos_anchor/cost_anchor/platform_payout)
│   ├── resolvers/        # priority 解析器 + layered_resource
│   ├── dimensions/       # 时间/业务维度
│   ├── analytics/        # 归因分析
│   └── comparison/       # 期间对比
├── bq_reports/           # 报表层 = 数据排列组合
│   ├── shared/           # 报表引擎基础设施 (report_engine + cache)
│   ├── utils/            # BQ 客户端 + ERPNext API
│   └── *.py              # 19 个报表入口
├── bom_pipeline/         # 生产管线 (clean_bom 单源, payment 锚定实收)
├── external_sales/       # 外部销售 (被 bq_exporter 消费)
├── utils/                # 跨层共享 (resource_adapter)
├── scripts/adhoc/        # 一次性审计/对账/接入脚本
├── resources/            # 活配置 + wallace.{date}/ 归档
└── tests/                # 584 测试
```

- [ ] **Step 3: 修 README.md**

同步顶层目录说明,突出"数据架构优先"原则。

- [ ] **Step 4: 全量验证**

```bash
PY=/home/weifashi/hwt/analysis/venv/bin/python
$PY -m unittest discover -s tests 2>&1 | grep -E "^Ran|^OK|^FAILED"
# Expected: OK (584)
./run.sh --help  # 帮助能正常打印
```

- [ ] **Step 5: 提交**

```bash
git commit -m "$(cat <<'EOF'
docs: 修 run.sh + CLAUDE.md + README.md 对齐新结构

run.sh: venv/bin/python, 指向实际入口
CLAUDE.md §2: 全面重写, "数据架构优先"原则
README.md: 同步

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

- Spec 覆盖: 删(engine/sources/go) → Task 1; 搬(cache/report_engine/layered_resource) → Task 2-4; 归位(scripts) → Task 5; 修(run.sh/CLAUDE.md/README.md) → Task 6。✅
- 无占位符: 所有 import 更新都是精确的旧→新对,所有 git mv 命令都是完整路径。✅
- Type 一致: 每个 Task 使用相同的 `$PY` 指向,相同的验证命令。✅
- 红线守住: semantic 内部不改; bom_pipeline/external_sales 不动; tests 结构不动。✅
