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
│   ├── profit_margin_report.py        # 利润报表（引擎驱动版）
│   └── utils/
│       ├── bq_client.py               # BQ 客户端 + 代理
│       └── erpnext_api.py             # ERPNext 价格查询
├── utils/                             # 通用工具（可复用）
│   ├── cache.py                       # 文件缓存层
│   ├── resource_adapter.py            # 外部资源适配器
│   └── report_engine.py               # 报表引擎（并发查询 + 配置驱动 Excel）
├── resources/
│   ├── wallace.20260422/
│   │   ├── config.yaml                # 资源映射配置（门店名、fallback BOM）
│   │   └── 物品消耗计算结果_*.xlsx
│   └── reports/
│       └── profit_margin.yaml         # 列定义、合并规则、公式模板
├── exports/                           # 报表输出目录
└── .cache/bq_reports/                 # 缓存文件（自动创建）
```

### 3. 新报表开发流程

使用报表引擎，只需写三部分：

```python
from utils.report_engine import ReportEngine, load_sheet_config

# 1. SQL 模板
SQL = "SELECT ... FROM `{project}`.`{dataset}`.`ttpos_xxx` ..."

# 2. 聚合逻辑（自定义）
def aggregate(raw_rows):
    ...

# 3. 列配置（YAML）
# resources/reports/my_report.yaml
```

引擎封装了：代理设置、BQ 连接、并发查询、外部资源加载、Excel 样式、合并单元格、公式、条件格式、缓存。

### 4. 外部资源适配规则

客户提供的 Excel/CSV 格式变化时：
- **不修改 Python 代码**
- 修改 `resources/` 下的 YAML 配置
- 支持：列回退、多 sheet 读取、回退链、条件路由

### 5. 代理设置

所有脚本已内置代理：`http://127.0.0.1:7897`
不需要额外设置环境变量。
