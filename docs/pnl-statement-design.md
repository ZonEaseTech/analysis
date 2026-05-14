# 财务标准全量利润报表设计 (P&L Statement)

> 按财务标准的损益表分层，全量统计集团利润；每一层数字可回溯到源 SQL；
> 现有 `profit_margin` / `profit_by_price` 是按 SKU 维度看销售毛利的报表，
> 这个新入口是**按财务损益表结构看集团整体利润**。
> 状态：**设计稿，未实施**。归档日期 2026-05-13。
>
> **v2 升级（2026-05-13）**：基于 [pnl-primer-for-engineers.md](./pnl-primer-for-engineers.md)
> 的行业标准 audit，补齐 5 大遗漏：
> 1. 餐饮行业指标（Prime Cost / Food Cost % / Labor Cost %）
> 2. AOV（客单价）
> 3. MoM 对比下放到 Phase 1（不是 Phase 4）
> 4. 菜单工程矩阵作为 Sheet 5
> 5. 财务化格式（千分位 / 负数括号 / 关键行加粗）

## 为什么单独搞一个入口

现有报表的定位：

- **`profit_margin`**：按 (店, SKU, BOM 物料) 维度看利润，**适合店长/采购**找成本异常
- **`profit_by_price`**：按 (店, SKU, 价格档) 维度看利润，**适合营销**看价格策略效果
- ❌ 没有**集团合并损益表**视角——老板/财务看不到一个 P&L 数字

新入口 `pnl_statement` 的定位：

- **按财务损益表结构**：GMV → Net Sales → Gross Profit → Contribution Margin → Operating Income
- **集团一行 / 按渠道一行 / 按店一行**：自上而下穿透
- **每一层数字带源标识**：出问题立刻回溯到哪一层、哪段 SQL、哪个原始字段

## 损益表分层（业界标准 + 我们能做到的层）

```
┌── 第 1 层：销售域（ttpos 数据 ✅）─────────────────────
│
│   GMV / 总营业额                          (堂食 + 外卖合并)
│   ├─ 堂食营业额                            sale_line.shop_sales.sales_price
│   └─ 外卖营业额                            takeout_line.takeout_sales.sales_price
│
│   减项：销售域可扣减损失
│   ├─ 赠品金额          (堂食)             sale_line.free_amount
│   ├─ 赠送金额          (堂食)             sale_line.give_amount
│   ├─ 退款金额          (堂食)             sale_line.refund_amount
│   ├─ 调价折扣          (堂食)             sale_line.discount_amount
│   └─ 外卖取消订单金额  (外卖)             takeout_line.cancelled_amount
│
│   = 净销售额 / Net Sales                  total_line.revenue
│     (= ttpos actual_sale_amount)
├──────────────────────────────────────────────────────
│
├── 第 2 层：成本扣减（ttpos + BOM × 销量 ✅）──────────
│
│   物料成本 COGS                           Σ bom_num × unit_price × qty
│   ├─ 堂食物料成本                          按 dine_qty 算
│   └─ 外卖物料成本                          按 take_qty 算
│
│   = 销售毛利 / Gross Profit               ← 当前报表能算到这一层
│     Gross Margin % = Gross Profit / Net Sales
├──────────────────────────────────────────────────────
│
├── 第 3 层：渠道变动成本（外部数据，目前缺失 ⚠️）──────
│
│   ⚠️ 平台抽佣  (Grab / LINE MAN / Shopee)  需平台月度对账单
│   ⚠️ 平台配送费分担                        需平台月度对账单
│   ⚠️ 支付通道费 (Robinhood / 银行扣款)     需银行/支付通道账单
│
│   = 贡献毛利 / Contribution Margin        Gross Profit - 变动成本
│     Contribution Margin % = / Net Sales
├──────────────────────────────────────────────────────
│
├── 第 4 层：固定运营成本（财务系统，目前缺失 ⚠️）──────
│
│   ⚠️ 房租
│   ⚠️ 人力 (工资 / 社保)
│   ⚠️ 水电气
│   ⚠️ 营销 / 推广 (集团/区域分摊)
│
│   = 经营利润 / Operating Income (EBIT)    Contribution Margin - 固定成本
├──────────────────────────────────────────────────────
│
└── 第 5 层：财务/税务（往下不再做 ❌）──────────────────
    ❌ 利息 / 折旧 / 摊销 / 所得税            一般不在经营分析里
    = 净利润 / Net Income
```

**我们当前能做到第 2 层（销售毛利）**。第 3 层往下要外接数据源。设计时**留好槽位**，数据接入时插槽即可，不需要重做。

## 入口设计

### CLI

```bash
venv/bin/python -m bq_reports.pnl_statement \
    --month 2026-04 \
    --output exports/pnl_202604.xlsx \
    [--shops shop1,shop2,...]      # 默认全集团
    [--channel both|dine|takeout]  # 渠道过滤
```

### 输入

- 时间范围：`--month YYYY-MM` 或 `--start-date / --end-date`
- 可选过滤：店列表、渠道
- 资源配置：跟 profit_margin 同一份 `resources/wallace.YYYYMMDD/config.yaml`

### 输出 Excel 结构（5 个 Sheet — v2 升级）

#### Sheet 1: 集团损益表（P&L Statement）

按 P&L 分层一行一行往下走，**每行 5 列**（金额 / % Net Sales / MoM / YoY / 备注），关键节点加粗：

```
┌───────────────────────────────────────────┬──────────────┬──────────┬──────────┬──────────┬──────────┐
│ 项目                                      │ 金额          │ % Net    │ MoM      │ YoY      │ 备注/状态 │
│                                           │ (千 THB)      │ Sales    │          │          │          │
├───────────────────────────────────────────┼──────────────┼──────────┼──────────┼──────────┼──────────┤
│ ▌GMV / 总营业额                            │     36,233   │   X%     │  +X.X%   │  +X.X%   │          │
│     ├─ 堂食营业额                          │     ~28,000  │   X%     │  +X.X%   │  +X.X%   │          │
│     └─ 外卖营业额                          │      ~8,233  │   X%     │  +X.X%   │  +X.X%   │ Mix 22.7%│
│   减 赠品/赠送金额                         │       (XXX)  │  -X.X%   │          │          │          │
│   减 退款金额                              │       (XXX)  │  -X.X%   │          │          │          │
│   减 调价折扣 (含番茄炸鸡桶等)             │       (XXX)  │  -X.X%   │          │          │ 集团促销 │
│   减 外卖取消订单                          │       (XXX)  │  -X.X%   │          │          │          │
├───────────────────────────────────────────┼──────────────┼──────────┼──────────┼──────────┼──────────┤
│ ▌净销售额 (Net Sales)                    ▌│     X,XXX    │  100.0%  │  +X.X%   │  +X.X%   │ ★ 对账锚 │  ← 加粗
├───────────────────────────────────────────┼──────────────┼──────────┼──────────┼──────────┼──────────┤
│   减 物料成本 (COGS / Food Cost)           │     (X,XXX)  │  -XX.X%  │  +X.X%   │  +X.X%   │          │
│     ├─ 堂食物料成本                        │       (XXX)  │   -X.X%  │          │          │          │
│     └─ 外卖物料成本                        │       (XXX)  │   -X.X%  │          │          │          │
├───────────────────────────────────────────┼──────────────┼──────────┼──────────┼──────────┼──────────┤
│ ▌销售毛利 (Gross Profit)                 ▌│     X,XXX    │   XX.X%  │  +X.X%   │  +X.X%   │ ⭐ 关键   │  ← 加粗
│   Gross Margin %                          │              │   65-70% │          │          │ 行业健康 │
├───────────────────────────────────────────┼──────────────┼──────────┼──────────┼──────────┼──────────┤
│   减 ⚠️ 平台抽佣 (估算 25%×外卖)           │     (X,XXX)  │   -X.X%  │          │          │ 估算值   │
│   减 ⚠️ 配送费分担                         │        N/A   │          │          │          │ 待接入   │
│   减 ⚠️ 支付通道费                         │        N/A   │          │          │          │ 待接入   │
├───────────────────────────────────────────┼──────────────┼──────────┼──────────┼──────────┼──────────┤
│ ▌贡献毛利 (Contribution Margin) — 估算   ▌│     X,XXX    │   XX.X%  │          │          │ 估算     │
├───────────────────────────────────────────┼──────────────┼──────────┼──────────┼──────────┼──────────┤
│   减 ⚠️ 房租                              │        N/A   │          │          │          │ 待接入   │
│   减 ⚠️ 人力 (Labor)                      │        N/A   │          │          │          │ 待接入   │
│   减 ⚠️ 水电气                            │        N/A   │          │          │          │ 待接入   │
│   减 ⚠️ 营销                              │        N/A   │          │          │          │ 待接入   │
├───────────────────────────────────────────┼──────────────┼──────────┼──────────┼──────────┼──────────┤
│ ▌经营利润 (Operating Income)             ▌│        N/A   │          │          │          │ 待接入   │  ← 加粗
└───────────────────────────────────────────┴──────────────┴──────────┴──────────┴──────────┴──────────┘
```

**财务化格式规则**（必须落到 yaml）：

- 金额单位：千 THB（绝对数太大不利于阅读）
- 千分位：`36,233`
- 负数括号：`(55,384)` 不是 `-55,384`
- 百分比 1 位小数：`-0.2%`
- 关键节点加粗：Net Sales / Gross Profit / Contribution Margin / Operating Income
- 减项内缩 4 空格 + "减 " 前缀
- 红色字体：占比 > 健康阈值的项（如 Food Cost > 35%）
- N/A 显式：未接入数据写 `N/A` + "待接入"，不留空

每一行单元格附带 **`comment` 注解**：

- 数字来源：哪个 BQ table / 哪个 semantic CTE / 哪个聚合函数
- 计算公式
- 缺失说明：N/A 时注明"需 XXX 数据，预计 YYY 时接入"

---

#### Sheet 1.5: 关键比率与行业基准对比（KPI Dashboard）

**这一块是非财务老板最爱看的部分**——一眼看出"我们健康吗"：

```
┌─────────────────────────────────────┬──────────┬──────────┬──────────┬──────────┬────────┐
│ 指标                                │ 本月       │ 上月       │ MoM      │ 行业基准   │ 评估    │
├─────────────────────────────────────┼──────────┼──────────┼──────────┼──────────┼────────┤
│ Gross Margin %                       │  XX.X%   │  XX.X%   │  +X.Xpp  │  60-70%  │ ✅ 健康 │
│ Food Cost % (COGS / Net Sales)       │  XX.X%   │  XX.X%   │  +X.Xpp  │  28-35%  │ ✅      │
│ Labor Cost %  (待接入)               │   N/A    │   N/A    │          │  25-30%  │  ⚠️    │
│ Prime Cost % (Food+Labor)  (待接入)  │   N/A    │   N/A    │          │  60-65%  │  ⚠️    │
│ Operating Margin %  (待接入)         │   N/A    │   N/A    │          │   8-15%  │  ⚠️    │
├─────────────────────────────────────┼──────────┼──────────┼──────────┼──────────┼────────┤
│ AOV / Average Check (客单价)         │  XXX     │  XXX     │  +X.X%   │     —    │        │
│   ├─ 堂食 AOV                        │  XXX     │  XXX     │          │          │        │
│   └─ 外卖 AOV                        │  XXX     │  XXX     │          │          │        │
│ 客户数（订单数）                     │  XXX,XXX │  XXX,XXX │  +X.X%   │          │        │
├─────────────────────────────────────┼──────────┼──────────┼──────────┼──────────┼────────┤
│ Channel Mix                          │          │          │          │          │        │
│   堂食占比                           │  XX.X%   │  XX.X%   │  +X.Xpp  │          │        │
│   外卖占比                           │  XX.X%   │  XX.X%   │  +X.Xpp  │          │        │
│ Effective Take Rate (估算)           │  ~25%    │  ~25%    │          │  20-30%  │ 估算   │
├─────────────────────────────────────┼──────────┼──────────┼──────────┼──────────┼────────┤
│ Same-Store Sales Growth (待月份累积) │   N/A    │          │          │   +5%    │        │
│ 新店数                               │   X      │          │          │          │        │
│ 总店数                               │   86     │          │          │          │        │
└─────────────────────────────────────┴──────────┴──────────┴──────────┴──────────┴────────┘
```

> `pp` = percentage point（百分点），财务标准缩写。`+5pp` 跟 `+5%` 不是一回事——
> 60% → 65% 是 +5pp 但只是 +8.3%。

#### Sheet 2: 按店损益

每店一行，每个 P&L 层一列。便于：

- 按店排名找异常
- 找"销售毛利率最高/最低"的店
- 找"赠送/退款金额异常大"的店

#### Sheet 3: 按渠道损益对比

```
                堂食             外卖           合计
GMV             X (77.3%)        X (22.7%)      X
Net Sales       X                X              X
COGS            X                X              X
Gross Profit    X                X              X
Gross Margin%   X%               X%             X%
变动成本估算    ~0               ~25%×Net      (估)
贡献毛利估算    X                X              X
```

直接回答"外卖到底赚不赚钱"——即使抽佣是估算值，也比当前一无所知好。

#### Sheet 4: 菜单工程矩阵（Menu Engineering — v2 新增）

按"销量 × 毛利率"四象限分类，回答"哪些 SKU 应该主推/下架/重新定价"：

```
                                  毛利率 (Gross Margin %)
                       低毛利            高毛利
                       ────────────────────────────
        高销量    │  🐴 Plowhorses    │  ⭐ Stars       │
       (前 20%)   │  走量低利           │  旗舰产品        │
                  │  (建议提价或换料)    │  (主推)         │
                  ├────────────────────┼────────────────┤
        低销量    │  🐕 Dogs          │  🧩 Puzzles     │
       (后 20%)   │  双输              │  好货卖不动      │
                  │  (考虑下架)         │  (营销推一下)   │
                  ────────────────────────────────────

输出列：
  店 | SKU | 销量 | 毛利 | 毛利率 | 销量分位 | 毛利率分位 | 象限 | 建议动作
```

按集团聚合一份，每店再细分一份。这是**菜单/产品决策**的标准框架，餐饮老板和品类经理都看。

> 这一 Sheet 不在 Phase 1 必须，但实施起来轻——已有 profit_by_price 的 SKU 级数据，
> 加个分位计算 + 象限分类公式即可。建议 Phase 1 末尾交付。

---

#### Sheet 5: 数据来源审计

每一层数字的 **数据血缘**：

```
项目              │ 数据源 (BQ table)             │ Semantic CTE         │ 字段
──────────────────┼────────────────────────────────┼──────────────────────┼────────────────────────────
GMV 堂食          │ ttpos_statistics_product       │ shop_sales_cte       │ sales_price
GMV 外卖          │ ttpos_takeout_order_item       │ takeout_sales_cte    │ sales_price (state IN active)
退款金额          │ ttpos_statistics_product       │ shop_sales_cte       │ refund_amount
COGS              │ ttpos_product_bom              │ bom_sql + qty 加权    │ Σ bom_num × unit_price × qty
平台抽佣 (估算)   │ —                              │ —                    │ commission_rate × takeout_sales
平台抽佣 (真值)   │ ⚠️ 平台对账单（待接入）         │ —                    │ —
```

出问题时，看哪一行数字异常 → 立刻定位到来源表/CTE → 直接跑 SQL 验证。

## 实施路线（分两期）

### Phase 1：能做到 Gross Profit + 关键比率 + MoM（3-4 天 — v2 升级）

**v2 升级范围**：

- ✅ 第 1 层 P&L（GMV → Net Sales）
- ✅ 第 2 层 P&L（Net Sales → Gross Profit）
- ✅ 第 3 层估算（按行业抽佣率算贡献毛利估算值）
- ✅ Sheet 1.5 KPI Dashboard（关键比率 + 行业基准对比）
- ✅ MoM 对比（不再延到 Phase 4）
- ✅ AOV / Channel Mix
- ✅ 财务化格式（千分位、负数括号、关键行加粗、N/A 显式）
- ✅ Sheet 4 菜单工程矩阵（轻量级，复用 profit_by_price 数据）
- ⚠️ YoY 暂返 N/A（数据满 12 个月后自动启用）
- ⚠️ Prime Cost / Labor Cost % 等需 Labor 数据的指标返 N/A

依赖：现有 semantic 层 + 现有 BOM 数据。

**新增文件**：

```
bq_reports/
└── pnl_statement.py                  # 主入口

semantic/aggregations/
├── pnl_layers.py                     # 各 P&L 层聚合（GMV / Net Sales / Gross Profit）
└── kpi_ratios.py                     # 比率计算（Gross Margin / Food Cost % / AOV / Mix）

semantic/comparison/                  # 新增 namespace
└── period_compare.py                 # MoM / YoY 对比

resources/reports/
└── pnl_statement.yaml                # 5 个 sheet 的列定义 + 财务化格式规则

tests/
├── test_pnl_layers.py                # P&L 各层恒等式
├── test_kpi_ratios.py                # 比率计算正确性
└── test_pnl_statement_smoke.py       # 端到端 smoke
```

**关键设计点**：

1. **不污染现有 entities**：复用 `sale_line` / `takeout_line` / `total_line` / `bom` / `combo`，
   不新建 SQL，只新建 **aggregation** 层把这些字段往 P&L 结构汇总。

2. **可回溯设计**：

   ```python
   @dataclass
   class PnlLayer:
       name: str                # 显示名
       amount: float            # 金额
       source_table: str        # BQ 来源表
       source_cte: str          # semantic 层 CTE
       formula: str             # 计算公式（人类可读）
       confidence: str          # "actual" | "estimated" | "n/a"
   ```

   每个数字带这 5 个属性，Excel 注释和审计 Sheet 直接读这个。

3. **恒等式校验复用 `semantic/validators`**：P&L 各层之间的减法关系
   （`GMV − 损失项 = Net Sales`，`Net Sales − COGS = Gross Profit`）
   写成新的 identities，纳入 `DEFAULT_IDENTITIES`。

4. **缺失层占位**：第 3 / 4 层有数据时输出真值，无数据时输出 `N/A` + 占位说明，
   不抛错——让客户/老板看到"这里缺什么"。

### Phase 2：接入第 3 层变动成本（需外部数据 + 1-2 周）

依赖：平台月度对账单 + 银行流水。

- 把对账单 CSV/Excel 通过 `utils/resource_adapter` 接进来
- 在 `semantic/settlement/` 下新建结算域 entity（platform_payout / bank_statement）
- `pnl_layers.py` 第 3 层从结算域取数
- 没拿到对账单时用 **估算抽佣率** (Grab 30% / LINE MAN 25% / Shopee 20%)，标 `confidence=estimated`

### Phase 3：接入第 4 层固定成本（需财务 ERP + 时间线 TBD）

依赖：客户/老板提供房租 / 人力 / 水电的财务系统出口。

- 这一层是按店/月固定值，不需要按 SKU 拆
- 可以先用 YAML 静态配置（每店每月几个固定值），后期再对接 ERP API

## 现有报表 vs 新入口的关系

```
┌───────────────────────────────────────────────────────┐
│                  ttpos 销售域数据                       │
│  (ttpos_statistics_product / ttpos_takeout_order_item) │
└─────────────────────────┬─────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   sale_line       takeout_line        bom + combo
        │                 │                 │
        └────────┬────────┘                 │
                 ▼                          │
            total_line                      │
                 │                          │
                 ▼                          │
              merged ─────────────┬─────────┘
                                  │
       ┌──────────────────────────┼──────────────────────────┐
       ▼                          ▼                          ▼
  profit_margin              profit_by_price             【pnl_statement】
  (店 × SKU × BOM 物料)      (店 × SKU × 价格档)         (集团 × P&L 层)
  适合店长/采购               适合营销                     适合老板/财务
```

新入口跟现有报表是**互补**关系，不替代。三者**共享 semantic 层和 BOM 数据**，
只是聚合维度不同。

## 校验恒等式（P&L 内部对账）

新加 identities 到 `semantic/validators/identities.py`：

```python
PNL_IDENTITIES = [
    Identity(
        name="P&L Net Sales 拆分",
        formula="GMV − 赠品 − 赠送 − 退款 − 调价折扣 − 外卖取消 = Net Sales",
        # ttpos actual_sale_amount 等价于 Net Sales
        severity=Severity.MUST_FIX,
    ),
    Identity(
        name="P&L Gross Profit 拆分",
        formula="Net Sales − COGS = Gross Profit",
        severity=Severity.MUST_FIX,
    ),
    Identity(
        name="P&L 堂食外卖加和",
        formula="堂食 GMV + 外卖 GMV = 总 GMV",
        severity=Severity.MUST_FIX,
    ),
]
```

每次导出**必跑**，console 至少打印 ✅/🔴。

## 跟 ttpos 数据的对账锚

P&L 算出来的 Net Sales 必须跟 ttpos 后台 `CountSale` 接口 + `CountTakeoutSale` 接口
返回的 `TotalReceivedAmount` 数值对齐。新增校验：

```python
# 跟 ttpos 后台对齐校验
TTPOS_RECONCILIATION = Identity(
    name="P&L Net Sales vs ttpos TotalReceivedAmount",
    formula="P&L Net Sales == ttpos CountSale.TotalReceivedAmount",
    tolerance=1.0,   # 单位是元
    severity=Severity.MUST_FIX,
)
```

数据源参考 [profit-report-takeout-semantics.md](./profit-report-takeout-semantics.md) 里
追到的 `CountTakeoutSale` 调用链。

## 不在范围内（明确说"不做"）

- **跨币种**：华莱士目前全 THB，不做币种换算
- **门店层级以下**：不做"按收银员/按时段"的 P&L（Daypart 是后续单独课题）
- **预算 vs 实际**：不做财务预算对账，那是 ERP 的活
- **三表合一**：只做 P&L（Income Statement），不做 Balance Sheet / Cash Flow
- **YoY 同比**：数据未满 12 个月，自动返 N/A（v2 升级：MoM 不在此列，下放 Phase 1）

## 关键决策点（实施前需用户确认）

1. **是否要在 Phase 1 就先做"估算抽佣"的第 3 层占位**？
   - 推荐：✅ 做。让老板立刻看到"按 25-30% 估算的话外卖大概亏多少"，建立心理预期
2. **资源配置路径**：用 `resources/wallace.YYYYMMDD/config.yaml`，还是 P&L 专属配置？
   - 推荐：复用现有 wallace 配置 + 新增 `pnl:` 段（commission_rate / fixed_costs 等）
3. **Excel 模板风格**：跟现有 profit_margin 视觉风格一致，还是更财务化（紧凑表头、分层缩进）？
   - 推荐：财务化。这份给老板/财务看，跟店长看的应该不一样
4. **跟 ERP 对账边界**：P&L 算出来的 Gross Profit 是否要跟客户 ERP 系统对账（如果他们有）？
   - 待沟通

## 相关文档

- [profit-margin-reconciliation-checklist.md](./profit-margin-reconciliation-checklist.md) — F vs G 对账方法论
- [profit-report-takeout-semantics.md](./profit-report-takeout-semantics.md) — 外卖口径 + ttpos 对账锚
- [bigquery-export-guide.md](./bigquery-export-guide.md) — 当前所有 BQ 导出入口
- [CLAUDE.md](../CLAUDE.md) — 项目结构（semantic/ 分层）+ 校验器规则
