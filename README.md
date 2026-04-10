# 华莱士 BigQuery 分析工作区

## 目录结构

```
analysis/
├── venv/                       # Python 虚拟环境
├── scripts/                    # 报表脚本
│   ├── report_bom_sales_bq.py      # BOM 商品销量报表
│   ├── report_daily_item_sales_bq.py  # 门店单品销量按日明细
│   ├── report_sales_simple_bq.py     # 销售业绩明细（原始订单行）
│   ├── export_sales_takeout.py       # 外卖平台销售数据导出
│   └── export_takeout_revenue.py     # 外卖收入报表导出
├── utils/                      # 工具函数
│   └── bq_client.py                # BigQuery 客户端配置（共用）
├── templates/                  # 模板文件
│   └── template_analysis.py        # 分析脚本模板
├── docs/                       # 文档
│   ├── query-patterns.md           # 查询模式参考
│   └── schema-reference.md         # 数据库结构参考
├── sql/                        # SQL 查询文件
│   └── takeout_revenue_query.sql   # 外卖收入查询
├── exports/                    # 导出文件（报表输出）
├── resources/                  # 资源文件（输入数据）
└── README.md                   # 本文件
```

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
