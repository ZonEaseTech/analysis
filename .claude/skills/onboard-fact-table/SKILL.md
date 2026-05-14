---
name: onboard-fact-table
description: 接入市场/客户透传的新事实表 (Excel/CSV) — 摸表结构、写 adapter、注册到 resolvers.yaml、跑测试、对账。让"接新表"从 1-2 天降到 1-2 小时。
triggers:
  - 新的 BOM 表
  - 新的物料价
  - 新的抽佣率
  - 客户给了一份 Excel
  - 市场扔表
  - 接入新事实
  - fact_overrides
  - 新的成本数据
---

# 接入新事实表 — Onboard Fact Table

## 适用场景

客户/市场/财务给一份 Excel/CSV/yaml，要把它接进利润报表的数据流。典型例子：

- 5 月物料调整价表（new material unit price）
- 套餐配方修正表（combo override）
- 人力成本表（labor_cost）
- 平台抽佣率表（commission_rate by store/platform）
- 商家服务费 / 配送费分摊 表

## 9 步流程

执行时必须**逐步走完**，不许跳。每步失败或不确定就停下问用户。

### 步骤 1：摸表结构

```bash
venv/bin/python -c "
import openpyxl
wb = openpyxl.load_workbook('PATH', data_only=True)
for sn in wb.sheetnames:
    ws = wb[sn]
    print(f'=== {sn} ({ws.max_row} 行 × {ws.max_column} 列) ===')
    print('Headers:', [c.value for c in ws[1]])
    for r in list(ws.iter_rows(min_row=2, max_row=5, values_only=True)):
        print('  ', r)
"
```

输出 sheet 名、列名、前 5 行数据。**贴出来给用户看**，等用户确认列含义后再下一步。

### 步骤 1.5：格式判断 — 直接接 vs 先清洗归档

客户 Excel 分两种,走法不同：

| 类型 | 信号 | 走法 |
|---|---|---|
| **A. 规整** | header 在第 1 行、一行一记录、列布局统一 | 直接写 mapping，跳到步骤 2 |
| **B. 混乱** | 多 sheet / 列偏移变体 / 商品名稀疏跨行承接 / 一个 sheet 多种布局 | **先清洗成标准 CSV 再接** |

**走 B 时的清洗 + 归档规范**（这次"市场补充 BOM"踩出来的）：

1. **归档原始**：`cp` 到 `resources/wallace.<日期>/<原名>_原始.xlsx`
   — 留本地审计，**不进 git**（原始 xlsx 体积大，是 repo 膨胀的真凶）
2. **写一次性清洗脚本**：`scripts/adhoc/clean_<topic>.py`
   — `RAW` 读上面的归档位（**不读** `/workspace/data/uploads/` 临时 UUID 路径），脚本可重跑
   — 客户给修订版时：覆盖 `_原始.xlsx` 重跑脚本即可
3. **输出标准 CSV**（不是 xlsx）：`resources/wallace.<日期>/<原名>.csv`
   — CSV 纯文本能 `git diff`（看出事实表改了哪几行），读取快（不用 openpyxl 解 zip/xml），体积小
   — 编码用 `utf-8-sig`，Excel 双击也不乱码
4. **git 跟踪**：原始 xlsx 不进（`.gitignore` 的 `resources/wallace.*/*` 默认挡）；
   清洗后 CSV + 清洗脚本 `git add -f` 进
5. **config 用 `adapter: csv`** 接清洗产物（CSVAdapter 已就绪，支持列回退）

### 步骤 2：业务分类

跟用户**显式确认**这表属于哪一类（影响接入位置）：

| 业务类别 | resolver 名 | 注册位置 |
|---|---|---|
| BOM 数量调整 | `bom_qty` | `config.yaml::bom_sources` |
| 物料单价 | `material_unit_price` | `config.yaml::material_price_sources` |
| 套餐结构修正 | `combo_structure` | `resolvers.yaml::resolvers.combo_structure` |
| 平台抽佣率 | `commission_rate` | `resolvers.yaml::resolvers.commission_rate` |
| 人力成本 | `labor_cost` | `resolvers.yaml::resolvers.labor_cost` |
| 店属性 (类型/区域) | `store_attribute` | `resolvers.yaml::resolvers.store_attribute` |
| 营收手工调账 | `revenue_adjustment` | `resolvers.yaml::resolvers.revenue_adjustment` |
| 其它 | **停下问用户** | — |

### 步骤 3：adapter 选型

扫 `utils/resource_adapter.py` 看现有 adapter 列表：

```bash
grep -n "^class .*Adapter\|^def.*adapter\|@register_adapter" utils/resource_adapter.py
```

- 列名/结构匹配现有 adapter → **复用**
- 不匹配 → 新写一个 adapter（继承 `BaseAdapter` 或 callable，50 行内）
- 新 adapter 写好后**先写单测**（`tests/test_resource_adapter.py`），通过再继续

### 步骤 4：写 mapping yaml

⚠️ **唯一活配置 = `resources/config.yaml`**（统一后只有这一份，不要再往
`resources/wallace.*/` 里放 config.yaml）。在这份里加配置段：

```yaml
material_price_sources:    # 或 bom_sources / fact_overrides 之一
  - name: <清洗后文件名>    # 用文件 basename, 不要编标签 (报表 BOM来源 列直接可追溯)
    priority: 100          # 数字大 = 更权威
    adapter: csv           # 清洗产物走 csv; 规整原始文件可直接 excel
    path: "resources/wallace.<日期>/<原名>.csv"
    # match_mode: exact    # 客户精确列出的商品名 → exact, 避免短名误命中长 key
    mapping:
      material_code: 物料编号    # csv column → field
      unit_price: 单价
```

**priority 规则**：
- 客户手工核对版 (最权威) → 100-200
- ERPNext API → 50
- BQ 原生 BOM → 0
- 兜底估算 → -10

### 步骤 5：注册到 resolvers.yaml（如果用 fact_overrides 通道）

新业务类别（commission_rate / labor_cost 等）通过 P3 fact_overrides 接入：

```yaml
# resources/wallace.YYYYMMDD/resolvers.yaml
resolvers:
  <category_name>:
    - kind: dict
      name: <source 名>
      priority: 100
      data: {...}        # 或 path + adapter 外接文件
```

### 步骤 6：业务白名单（业务安全 review）

**这一步必须人工确认**，AI 不要自动加。

新业务类别要在报表脚本的 `allowed_categories` 白名单里出现才生效：

```python
# bq_reports/pnl_statement.py:main() 之类的位置
resolvers = load_resolvers_from_yaml(
    "resources/wallace.*/resolvers.yaml",
    allowed_categories=[
        "commission_rate",
        "labor_cost",       # ← 新加这一行
    ],
)
```

加白名单 = 业务批准生效。**没人 review 不许 commit**。

### 步骤 7：跑测试

```bash
venv/bin/python -m unittest tests.test_resolver_loader
venv/bin/python -m unittest tests.test_resolvers
venv/bin/python -m unittest discover tests   # 全套, 验证零 regression
```

**任一失败立刻停下**，不要硬改测试让它过。

### 步骤 8：跑一次实际报表对比

```bash
# 接表前 (备份当前输出)
venv/bin/python -m bq_reports.profit_margin_report --month YYYY-MM --summary \
    --output exports/before_onboard.xlsx

# 接表后 (新配置生效)
venv/bin/python -m bq_reports.profit_margin_report --month YYYY-MM --summary \
    --output exports/after_onboard.xlsx
```

### 步骤 9：数字对照 + sign-off

跑 SUM 对比关键聚合数字（**销量 / 营业额 / 实收金额 / 总成本 / 总利润**）：

```python
venv/bin/python -c "
import openpyxl
for label, path in [('Before', 'exports/before_onboard.xlsx'),
                    ('After',  'exports/after_onboard.xlsx')]:
    wb = openpyxl.load_workbook(path, data_only=True)
    for sn in wb.sheetnames:
        ws = wb[sn]
        headers = [c.value for c in ws[1]]
        for col in ['销量', '营业额', '实收金额', '总成本', '总利润']:
            if col in headers:
                idx = headers.index(col)
                total = sum((r[idx] or 0) for r in ws.iter_rows(min_row=2, values_only=True)
                            if isinstance(r[idx], (int, float)))
                print(f'{label:>8} | {sn:<6} | {col:<8} | {total:>15,.2f}')
"
```

**判断准则**：

| 差异 | 处理 |
|---|---|
| 销量 / 营业额 / 实收金额 完全不变 | ✅ 正常（新表只影响成本，不影响销售域）|
| 总成本变化 < 5% | ✅ 正常，让用户看下 top SKU 差异 sign-off |
| 总成本变化 5-20% | ⚠️ 停下让用户看具体哪些 SKU，可能新表覆盖范围出预期 |
| 总成本变化 > 20% | 🔴 **停下，强制 review**，可能 mapping 错或 adapter bug |

### 步骤 10（可选）：commit

确认 sign-off 后:

```bash
# 原始 xlsx 不进 git; 清洗 CSV / 清洗脚本 / config / 代码 才进
git add -f resources/wallace.YYYYMMDD/<原名>.csv
git add resources/config.yaml scripts/adhoc/ utils/resource_adapter.py bq_reports/<报表>.py tests/
git commit -m "feat(fact-overrides): 接入 <类别> <源名> (priority=<N>)

源: <文件名> from <客户/市场>, <日期>
影响: <几个 SKU 受影响, 总成本变化 X%>
注册位置: resolvers.yaml::<category>
白名单: <报表>.allowed_categories +1 (人 review by <用户>)

测试: <NNN>/<NNN> OK, parity 验证零 regression"
```

## 常见坑 (踩过的列在这)

1. **Excel 列名带 BOM**：UTF-8 BOM 让 `headers[0]` 不等于显示的列名。先用 `c.value.strip('﻿')` 清洗。
2. **数字列被 Excel 当字符串**：物料编号 "00123" 被读成 `123`。用 `str(c.value).zfill(N)` 或在 mapping 加 `force_str: true`。
3. **Sheet 名带不可见字符**：tabs / 换行 / 全角空格。用 `sn.strip()` 清洗。
4. **客户 Excel 有合并单元格**：openpyxl 读到的合并单元格只有左上角有值，其它格 None。用 `ws.merged_cells` 处理。
5. **mapping 字段名写错不报错**：adapter 默默返回空 dict，跑出来"没生效"。**步骤 9 数字对照能 catch 这个**——总成本完全没变就是 mapping 没生效。
6. **priority 跟现有源冲突**：扫 `_load_*_layers` 已有 priority 数字，避免重复。`priority=50` 跟 ERPNext 同优先级会按插入顺序解决，难复现。

## 业务安全边界

| 行为 | AI 能做 | 必须人确认 |
|---|---|---|
| 读 Excel 摸结构 | ✅ | — |
| 写 adapter | ✅ | 步骤 3 选型确认 |
| 写 mapping yaml | ✅ | — |
| 改 resolvers.yaml | ✅ | — |
| 加白名单 (`allowed_categories`) | ❌ | **必须人 review** |
| 跑测试 | ✅ | — |
| Sign-off 数字差异 | ❌ | **必须人 review** |
| Commit | ✅ | 但 push 前要人 review diff |

## 相关文档

- [bigquery-export](../bigquery-export/SKILL.md) — BQ 表结构 / SQL 模式速查
- [docs/architecture-evolution-roadmap.md](../../../docs/architecture-evolution-roadmap.md) — 整体平台架构
- [docs/profit-margin-reconciliation-checklist.md](../../../docs/profit-margin-reconciliation-checklist.md) — 数据对账方法论
- [docs/metrics-catalog.md](../../../docs/metrics-catalog.md) — 口径地图（每个指标的完整定义）
- [semantic/resolvers/loader.py](../../../semantic/resolvers/loader.py) — yaml-driven resolver builder
