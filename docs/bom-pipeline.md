# BOM Pipeline — Wallace 商品成本毛利分析

> 生产口径走同事脚本（payment 锚定实收）。v26→v40 多轮迭代后于 2026-06-26 收编为
> `bom_pipeline/` 包。本文档是该流程的完整说明。代码 README 见 `bom_pipeline/README.md`。

## 1. 背景与定位

华莱士（泰国）商品成本毛利分析。多轮迭代散落了几十个 xlsx/csv/脚本，2026-06-26
整理收编：可复用纯 Python 进 `bom_pipeline/`，数据按 `resources/wallace.<YYYYMMDD>/` 归档，
交付物落 `exports/`。

参见决策稿：`docs/superpowers/specs/2026-06-26-bom-pipeline-refactor-design.md`。

## 1.5 数据源谱系 — `clean_bom.csv` 从哪来

`clean_bom.csv` 是本流程的 BOM 单源，但它本身是一条**多轮迭代**的产物，不是一次成型：

```
ERP（ERPNext）──直接 load──▶ 初版全量 BOM（配方 + 物料 + 单价，按商品×物料展开）
                                  │
                                  ▼  反复人工纠正、改来改去（缺配方补、错物料删、渠道拆、单价对）
        历代 xlsx（按时间，均已归档 exports/_archive/）：
          Wallace_全量BOM_..._v26_脚本真源2.xlsx        (06-23, ~4493 行)
            └─ _已修正.xlsx
          clean_bom 1.xlsx                              (06-24, ~4470)
          bom表.xlsx / bom表1.xlsx                       (06-24, ~4465/4467)
          bom纠正.xlsx                                   (06-22, 3 行人工补丁)
          bom_with_erp_price.xlsx                        (06-25 10:02, 加 ERP 价格列)
          bom_with_erp_price_v4.xlsx                     (06-25 10:17, 价格定版)
                                  │
                                  ▼  落规范单源
                       resources/wallace.20260626/clean_bom.csv  (4466 行 / 14 列)
```

要点：

- **配方初始来自 ERP 直接 load**（ERPNext）：物料、配方结构、采购单价都从 ERP 拉出来铺成全量 BOM。
- **之后反复人工改**：补缺配方、删错物料、按堂食/外卖拆渠道、对单价。过程**非线性**，
  每一版 xlsx 是一次手工迭代的快照，具体改动散落在这些文件里（已全部归档 `exports/_archive/`，未删，可回溯）。
- **价格列**是其中 ERP 拉取 + 复刻算法（见 §4）那条支线的产物：`基价(原始)→×(1+margin)→×(1+税率)→ERPNext新单价`，
  最终写进 `clean_bom.csv` 的「物料单价」列。`clean_bom.csv` 已完整承接 `bom_with_erp_price_v4`（0 差异）。
- **往后这一段才是本文档 §3 起讲的可复跑流程**；本节这段上游是人工迭代史，靠归档 xlsx + git 回溯，
  不进自动管线。新月份要重建 BOM 时，应从 ERP 重新 load 起、而非手改这些历史 xlsx。

## 2. 目录结构

```
bom_pipeline/
├── wallace_bom_margin.py    # 成本毛利生成器
├── bom_rules.py             # 渠道过滤 / 物料删除规则
├── erpnext_price.py         # 物料采购成本复刻（calculateFinalItemUnitCost）
├── 名称映射_中英泰.json      # --lang en/th 时的名称映射
└── README.md

resources/wallace.20260626/
├── clean_bom.csv            # BOM 单源（商品×物料×消耗×单价×渠道）
├── sales_fixed.xlsx         # 修正销量表（多 sheet：堂食实收/外卖促销/套餐/单品）
├── recon.json               # 门店实收明细（实收对齐锚）
└── _archive/                # 历史版本

exports/Wallace商品成本毛利分析_2026-05_v40.xlsx   # 交付物
```

> 注：`sales_fixed` 是**多 sheet 工作簿**（`load_sales` 逐 sheet 读），不能转单 csv，保留 xlsx。
> 只有单表文件（BOM）用 csv。

## 3. 数据流与口径

### 3.1 总流程

```
clean_bom.csv → load_clean_bom() → 套 bom_rules 删除规则 → price + 渠道感知 BOM
sales_fixed.xlsx → load_sales() → 净销量 + 堂食/外卖实收率
recon.json → 实收对齐
        ↓
   build() → 堂食-单品 / 堂食-套餐 / 外卖-单品 / 外卖-套餐 四 sheet → exports/*.xlsx
```

### 3.2 成本口径

- 单份总成本 = Σ(消耗数量 × 物料单价)
- 物料单价 = `clean_bom.csv` 的「物料单价」列 = ERPNext 采购终价（见 §4）
- 套餐：clean_bom 已展开的套餐配方；缺则「明细组分 × 单品 BOM」拼，再缺模板补

### 3.3 实收对齐（recon）

把每店每渠道的实收率分子换成门店汇总口径实收（堂食 payment−退款−挂账 / 外卖 platform_total），
分母用毛利表该店该渠道实际原价和，使 Σ(原价×rate) = 门店汇总实收，4 sheet 合计与 ttpos shop 报表一致。

## 4. 价格口径 — erpnext_price.py（复刻 ttpos 后端）

复刻 `ttpos-server-go/ttpos-bmp/app/ttpos-erp/internal/logic/stock/item.go` 的
`calculateFinalItemUnitCost`，确保与后端 `GetItemUnitCost` 同口径。

```
净价 netCost = baseCost 依次套 ERPNext buying Pricing Rules
              （Percentage → ×(1+rate/100)；Amount → +amount）
终价         = netCost==0 或 taxRate==0 ? netCost : netCost × (1+taxRate/100)
税率         = 物料 Item Tax 缺失时兜底泰国 VAT 7%（defaultItemUnitCostTaxRate）
```

- `baseCost` = ERPNext Item Price（Buying 价表）`price_list_rate`，按匹配 UOM 取
- 默认 buying 规则 = Percentage 5%（`DEFAULT_BUYING_RULE`，由后端 Pricing Rule 决定）
- **验收**：对 `bom_with_erp_price_v4.xlsx` 每行用 (基价原始, 税率) 复算终价，与 `ERPNext新单价` 列
  逐行一致（4465/4465）。单测 `tests/test_erpnext_price.py`。

### 4.1 实时拉取（UOM + Item Price）

`erpnext_price.py` 只做「基价 → 终价」纯算法（可离线复跑、可单测）。实时拉 Item Price +
UOM 换算复用 `bq_reports/utils/erpnext_api.py` / `semantic/cogs/material_price.py`。
`bom_with_erp_price_v4.xlsx` 即上游拉取 + 本算法的产物。

## 5. 删除规则 — bom_rules.py

| 规则 | 语义 |
|------|------|
| `DINEIN_DEL` | 外卖才用的包材/酱料，堂食侧删除（通用行降级为仅外卖，堂食专属行丢弃） |
| `TAKEOUT_SINGLE_DEL` | 堂食才用的物料，外卖单品侧删除 |
| `PRODUCT_DEL` | 按商品名精确删指定物料 |

口径只在 `bom_rules.py` 改，不要把"特殊容忍"散落进报表脚本。

## 6. 运行与验证

运行命令见 `bom_pipeline/README.md §运行`。

- **复跑一致性闸门**：重构只移位置/抽规则，未改数 —— 输出须与上一版 v40 逐格 0 差异。
- **价格复刻闸门**：`venv/bin/python -m unittest tests.test_erpnext_price -v`（含 v4 基准）。

## 7. 已知遗留

- ~~`resolve_recon_path` 引用未定义 `BASE`~~ → 已修（2026-06-26）：改为相对 sales 同目录查找，
  兼容 `Wallace门店实收明细_<月>.json` 与 `recon.json`，不再依赖 sales 文件名含月份。
  回归测试 `tests/test_recon_resolve.py`。
- ~~`drop_*` 四个死函数~~ → 已删（2026-06-26）：clean_bom 时代前的过滤死代码，规则在 `bom_rules.py`。
- 根目录散落的历史 xlsx 已移入 `exports/_archive/` 归档保留（未删，多为同事/客户原件）。
