# bom_pipeline — Wallace 商品成本毛利分析（同事脚本收编版）

生产口径走这套（payment 锚定实收）。v26→v40 多轮迭代后于 2026-06-26 收编整理。

## 模块

| 文件 | 职责 |
|------|------|
| `wallace_bom_margin.py` | 成本毛利生成器：BOM 套销量算成本，实收对齐门店汇总，输出 4 sheet（堂食/外卖 × 单品/套餐） |
| `bom_rules.py` | 渠道过滤 / 物料删除规则（`DINEIN_DEL` / `TAKEOUT_SINGLE_DEL` / `PRODUCT_DEL`），口径在此收口 |
| `erpnext_price.py` | 物料采购成本复刻（`calculateFinalItemUnitCost`），与 ttpos 后端同口径 |

## 数据流

```
clean_bom.csv ──┐
                ├─ load_clean_bom() ─ 套用 bom_rules 删除规则 ─ price + 渠道感知 BOM ┐
                │                                                                    │
sales_fixed.xlsx ─ load_sales() ─ 净销量 + 堂食/外卖实收率 ───────────────────────┤
                │                                                                    ├─ build() ─ 4 sheet ─ exports/*.xlsx
recon.json ─────┴─ 实收对齐（堂食 payment−退款−挂账 / 外卖 platform_total）─────────┘

物料单价来源：clean_bom.csv 的「物料单价」列（已由 erpnext_price 口径算好的 ERPNext 终价）
             erpnext_price.py = 该单价的复刻算法：基价 × (1+margin) × (1+税率)，详见模块 docstring
```

## 运行

```bash
venv/bin/python bom_pipeline/wallace_bom_margin.py \
  --bom    resources/wallace.20260626/clean_bom.csv \
  --sales  resources/wallace.20260626/sales_fixed.xlsx \
  --recon  resources/wallace.20260626/recon.json \
  --out    exports/Wallace商品成本毛利分析_2026-05_v40.xlsx \
  --lang   zh        # zh / en / th 三语，名称映射读 bom_pipeline/名称映射_中英泰.json
```

## 验证

```bash
# 价格复刻单测（含 bom_with_erp_price_v4.xlsx 基准 4465/4465）
venv/bin/python -m unittest tests.test_erpnext_price -v

# 复跑一致性：输出应与上一版 v40 逐格 0 差异（重构未改数）
```

## 口径真源

- 删除规则：`bom_rules.py`
- 价格算法：`erpnext_price.py` ← `ttpos-server-go/ttpos-bmp/app/ttpos-erp/internal/logic/stock/item.go`
- 完整文档：`docs/bom-pipeline.md`
