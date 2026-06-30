# 华莱士 BigQuery 分析工作区

## 目录结构

```
analysis/
├── semantic/                     # 数据架构核心 — 口径真源
│   ├── entities/                 #   CTE 工厂 (12 实体)
│   ├── cogs/                     #   成本解析 (material_price 4层 priority)
│   ├── aggregations/             #   聚合 (by_grain/pnl_layers/kpi_ratios)
│   ├── metrics/                  #   指标注册表 (5 yaml, 30 指标)
│   ├── validators/               #   恒等式 + 闸门
│   ├── reconciliation/           #   对账锚 (ttpos/cost/platform_payout)
│   └── resolvers/                #   priority 解析器
├── bq_reports/                   # 报表层 = 数据排列组合
│   ├── shared/                   #   报表引擎 (report_engine + cache)
│   ├── utils/                    #   BQ/ERP 客户端
│   └── [19 个报表入口]
├── bom_pipeline/                 # 生产管线 (clean_bom + payment 锚定)
├── external_sales/               # 外部销售接入 (huku)
├── utils/                        # 跨层共享 (resource_adapter)
├── scripts/adhoc/                # 审计/对账/接入工具
├── resources/                    # 配置 + data archives
├── tests/                        # 584 tests
├── docs/                         # 架构·计划·spec·审计
└── exports/                      # 导出物 (gitignored)
```

> 原则: **报表 = 不同数据的排列组合**。共享逻辑收口在 `semantic/`, 报表特有逻辑在 `bq_reports/` 内自洽。详见 `docs/pipelines-overview.md`。

## 快速开始

### 1. 激活虚拟环境

```bash
cd /Users/tao/Desktop/Projects/analysis
source venv/bin/activate
```

### 2. 运行报表脚本

```bash
# 默认统计上个月
python3 scripts/report_bom_sales_bq.py

# 指定月份
python3 scripts/report_bom_sales_bq.py 2026-03
```

### 3. 退出虚拟环境

```bash
deactivate
```

## 常用脚本说明

| 脚本 | 功能 | 输出行数 |
|------|------|----------|
| `scripts/report_bom_sales_bq.py` | BOM 商品销量（按商品聚合） | ~5,000 行 |
| `scripts/report_daily_item_sales_bq.py` | 门店单品销量按日汇总 | ~128,000 行 |
| `scripts/report_sales_simple_bq.py` | 原始订单明细（不去重） | ~600,000 行 |

## 创建新分析脚本

参考模板：

```python
#!/usr/bin/env python3
from utils.bq_client import get_bq_client, setup_proxy, STORE_UUIDS
from google.cloud import bigquery

PROJECT_ID = "diyl-407103"

def main():
    setup_proxy()
    client = get_bq_client()
    
    # 你的查询逻辑
    query = f"""
    SELECT * FROM `{PROJECT_ID}.shop1958987436032000.ttpos_sale_order_product`
    LIMIT 10
    """
    
    result = client.query(query).result()
    for row in result:
        print(row)

if __name__ == "__main__":
    main()
```

## 全局快捷方式（可选）

添加到 `~/.bashrc` 或 `~/.zshrc`：

```bash
alias analysis='cd /Users/tao/Desktop/Projects/analysis && source venv/bin/activate'
```

以后直接运行：
```bash
analysis
python3 report_bom_sales_bq.py
```

## 注意事项

1. **代理设置**：脚本已内置代理配置（`http://127.0.0.1:7897`）
2. **认证**：使用 `gcloud auth print-access-token`，确保持续登录状态
3. **输出文件**：保存在 `exports/` 目录下

## 依赖包

- `google-cloud-bigquery`
- `openpyxl`

如需安装其他包：
```bash
source venv/bin/activate
pip install pandas numpy matplotlib
```
