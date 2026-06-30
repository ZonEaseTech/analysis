# BOM Pipeline Refactor — 代码与配置整理

## 目标

将 v26→v40 多轮迭代产生的散落文件归整为可维护的结构。

## 最终结构

```
analysis/
├── bom_pipeline/                   # 可复用纯 Python 代码
│   ├── wallace_bom_margin.py       # 成本毛利生成器（含 load_clean_bom 等改动）
│   ├── erpnext_price.py            # calculateFinalItemUnitCost 复刻
│   ├── bom_rules.py                # BOM 规则引擎（渠道过滤、物料删除）
│   └── README.md                   # 流程图 + 运行命令
│
├── resources/
│   ├── config.yaml                 # 主配置（不动）
│   └── wallace.202606/             # 本月数据
│       ├── clean_bom.csv           # BOM 单源（当前版本）
│       ├── sales_fixed.csv         # 修正销量表（xlsx→csv）
│       ├── recon.json              # 实收对账
│       └── _archive/               # CSV 归档（历史版本）
│           ├── bom_with_erp_price_v4.csv
│           ├── clean_bom_v1.csv
│           ├── bom表_20260624.csv
│           └── sales_orig.csv
│
├── exports/
│   └── Wallace商品成本毛利分析_2026-05_v40.xlsx
│
└── docs/
    └── bom-pipeline.md             # 完整流程文档
```

## 文件来源映射

| 目标 | 来源 |
|------|------|
| bom_pipeline/wallace_bom_margin.py | scripts/wallace_bom_margin_同事版/wallace_bom_margin.py（抽规则后 import bom_rules） |
| bom_pipeline/erpnext_price.py | **复刻** ttpos-server-go/ttpos-bmp/app/ttpos-erp/internal/logic/stock/item.go `calculateFinalItemUnitCost`（仓库内无 v4 生成脚本，需重建） |
| bom_pipeline/bom_rules.py | 提取 load_clean_bom 中的 DINEIN_DEL / TAKEOUT_SINGLE_DEL / PRODUCT_DEL 规则 |
| resources/wallace.202606/clean_bom.csv | 当前使用的 clean_bom.csv |
| resources/wallace.202606/sales_fixed.csv | sales_fixed.xlsx → csv |
| resources/wallace.202606/recon.json | 原位置复制 |

## 清理项

- 根目录散落的 xlsx 文件 → 转为 csv 入 _archive/，原文件删除
- exports/ 旧版本 → 保留 v40，其余删除
- 同事目录旧产物 → _archive/ 或删除
- 脚本 diff 通过 git commit 记录

## 澄清结论（2026-06-26，何伟涛拍板）

原 spec 红线1（"不修改内部逻辑"）与"抽 bom_rules.py"互斥，已查实并定调：

1. **放弃红线1**：抽 DINEIN_DEL / TAKEOUT_SINGLE_DEL / PRODUCT_DEL 到 `bom_rules.py`，
   `wallace_bom_margin.py` 的 `load_clean_bom()` 改为 `from bom_rules import ...`。
   代价：必须用 v40 输出做**数值 diff 验证一致**，才算守住"可复跑"。
2. **价格模块现在重建**：`bom_with_erp_price_v4` 的生成逻辑仓库内无 .py 源（v4.xlsx 是产物，
   全仓 grep 不到生成脚本），所以 `erpnext_price.py` 是**复刻新建**，不是搬运。
   复刻基准 = Go 后端 `calculateFinalItemUnitCost`：
   `netCost = baseCost 依次套 Percentage/Amount margin 规则；netCost==0 或 taxRate==0 → 返回 netCost；否则 netCost×(1+taxRate/100)`。
   UOM 换算在上游（对应 v4.xlsx 列：基价(原始)→基价(两位)→适用税率%→ERPNext新单价 + ERP_UOM）。
   验收：重建输出与 `bom_with_erp_price_v4.xlsx` 的 `ERPNext新单价` 列逐行一致。

## 红线（更新后）

- ~~不修改 wallace_bom_margin.py 内部逻辑~~ → 改为：抽规则后 **v40 数值 diff 必须一致**
- 不修改 config.yaml
- 所有 xlsx 转 csv 保留完整列
- git 提交前验证 v40 可复跑
- `erpnext_price.py` 重建输出对齐 `bom_with_erp_price_v4.xlsx` ERPNext新单价列
