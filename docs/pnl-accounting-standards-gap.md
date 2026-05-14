# 财务 P&L 跟会计准则差异分析

> 给客户财务团队 review 的对接材料。我们已建好"工程师视角的管理会计 P&L 报表"
> (`bq_reports/pnl_statement.py`)，但**没经过财务专业 review**，下面列出 5 类已知
> gap 让你们指出哪里要修正。
>
> 状态：**P0 输入材料**，等待客户财务 sign-off 后实施 fix。归档日期 2026-05-13。

## 报表性质声明

**本报表为「管理会计 / 经营分析」用途，不等同于法定财务报表 (Statutory
Financial Statements)**。差异主要在：

- 收入确认时点（销售当天 vs T+N 平台结算时点）
- 会计科目映射（没跟 ERP / GL 系统科目代码挂钩）
- 含税口径（VAT 含/不含）
- 多主体合并消除项
- 调整分录（应计/预提/折旧/摊销）

法定财报需通过 ERP（如客户的会计系统）出。

## 5 类已知 gap

### Gap 1: 收入确认时点（ASC 606 / IFRS 15 / CAS 14）

**当前实现**：
- 堂食：用 `ttpos_statistics_product.complete_time`（POS 完单时间）确认
- 外卖：用 `ttpos_takeout_order.completed_time`（state=40 时）或 `accepted_time`（其他 state）

**问题**：
- 外卖平台 T+7~T+15 才结算到商家银行账户，**销售当天确认 vs 结算时点确认** 在
  现金/权责发生制下不同
- 外卖订单被退/取消跨期（4 月销售 / 5 月取消）目前算到 4 月 GMV 减项里
- 礼品卡 / 会员卡预充值 → 卡里钱什么时候算"收入"

**需财务确认**：
- [ ] 采用什么收入确认准则（ASC 606 / IFRS 15 / 中国 CAS 14）
- [ ] 销售当天确认 vs 平台结算时点确认 → 选哪一种
- [ ] 跨期退款/取消怎么处理（回冲销售 vs 单独损失项）

### Gap 2: 会计科目映射（Chart of Accounts）

**当前实现**：P&L 行用业务名（"营业额"、"COGS"、"平台抽佣" 等），没跟会计科目
代码挂钩。

**ERP/财务系统的对应**（中国会计准则示例）：
```
我们 P&L 行                         对应会计科目        会计科目代码
─────────────────────────────────────────────────────────────────
GMV / Gross Revenue                主营业务收入        6001
  Returns & Allowances             销售退回              6051
  Discounts                        销售折扣              6052
Net Sales                          (= 6001 − 6051 − 6052)
  COGS                             主营业务成本        6401
Gross Profit                       (derived)
  Platform Commission              销售费用-平台手续费 6604
  Delivery Fee Share               销售费用-配送费     ?
  Payment Processing               财务费用-手续费     6603
  Rent                             管理费用-租赁费     6701
  Labor                            主营业务成本-人工 / 管理费用-工资 ?
  Utilities                        管理费用-水电        6701
  Marketing                        销售费用-广告        6602
Operating Income (EBIT)
```

**需财务确认**：
- [ ] 客户用什么会计准则（IFRS / 美国 GAAP / 中国 CAS / 泰国 TFRS）
- [ ] 提供完整会计科目表 (Chart of Accounts)
- [ ] 给每个 P&L 行确认对应科目代码（让我们在 yaml 配置里加 `account_code` 字段）
- [ ] 平台抽佣到底进 **销售费用** 还是 **主营业务收入回冲** —— 这是个会计政策选择

### Gap 3: VAT 含税口径

**当前实现**：
- `ttpos_takeout_order.tax` 字段在 ttpos 里有，但**外卖侧 sale_event 没把税单独
  剥出来**
- 堂食 `product_sale_price` / `product_final_price` 是否含税不明确，sale_event
  直接 SUM

**泰国 VAT 7%**。业界标准：
- **收入 (Revenue / Net Sales)** 按**不含税**口径计 → 法定财报上的收入
- **现金流** 按**含税**口径计 → 顾客实付金额
- **应交税费**进负债类科目

**需财务确认**：
- [ ] ttpos 里 `product_sale_price` / `toi.price` **含税还是不含税**
- [ ] 我们 P&L 的 Revenue 行该按**不含税还是含税**展示
- [ ] 给我们一个 VAT 提取公式（如果不含税，要 `price / 1.07`？或者税字段已经存在）

### Gap 4: 审计追溯到原始凭证

**当前实现**：每个 P&L 数字带 `source_table` / `source_cte` / `formula` 元数据
（见 Sheet 6 数据来源审计），追溯到**字段级**。

**业界审计要求**：必须追到**原始凭证**级（每一笔订单/收据/发票）。

**Gap**：我们只追到"哪段 SQL 算的"，没追到"具体哪一条订单贡献了这 100 元"。

**需财务确认**：
- [ ] 客户审计师是否要求订单级追溯（如果是，我们要每个数字额外保留 `order_uuid`
      列）
- [ ] 是否接受"SQL + 缓存哈希"作为审计证据（dbt / Cube.dev 业界做法）

### Gap 5: 多主体合并

**当前实现**：按"店"聚合，53 家店全部塞一个集团 P&L。

**业界要求**：如果集团有多个法人主体（如华莱士可能有：
- 泰国本地法人 A（运营泰国门店）
- 香港控股公司 B（控股 A）
- 母公司 C（中国实体）），跨主体的关联交易要**消除** (Eliminations) 才能出**合并报表** (Consolidated)。

**需财务确认**：
- [ ] 53 家店分属哪些法人主体（每店一份 entity_uuid）
- [ ] 是否需要按主体聚合的 P&L（我们当前按"全集团"聚合一份，不分主体）
- [ ] 是否有关联交易需要消除（如果都是同一主体直营，无消除项）

## 修正影响评估

| 修正项 | 改动量 | 影响范围 |
|---|---|---|
| 会计科目代码 | yaml 加 `account_code` 字段 | 1 小时 (纯配置) |
| 调整科目（"赠品进推广费"） | `pnl_layers.py` 调一行 | 半天（含历史月份重跑对账） |
| VAT 含税切换 | sale_line / takeout_line CTE 加 `/(1+vat_rate)` | 1 小时 + 测试 |
| 收入确认时点切换 | CTE WHERE 改一处 | 半小时 |
| 退款单独列示（不回冲）| `pnl_layers.py` 移一行 | 2 小时 |
| 跟法定财报对账 | 加 `StatutoryPnlTieOutCheck` (P4 扩展) | 1 天 |
| 月度调节表 | 新增 Sheet | 1 天 |
| 多主体合并 | 加 entity 维度聚合 | 半天 |

**整体修正 ≈ 2-3 天**（详见 `docs/architecture-evolution-roadmap.md` 第 8 节）。

## 我们建议的对接流程

1. **客户财务团队读本文档 + 打开 `exports/pnl_202604_complete.xlsx` 看实际产物**
2. **回我们一份"需修正项清单"**（在每个 gap 下打勾哪些要改、哪些不要）
3. **提供 Chart of Accounts**（最关键，让我们做科目映射）
4. **提供一份历史月度财报**（让 `StatutoryPnlTieOutCheck` 实施时有 ground truth）
5. **提供平台对账单样本**（Grab/LINE MAN/Shopee 各 1 份，让 `PlatformPayoutCheck`
   实施时知道 Excel 列结构）

完成以上 5 步后，我们用 2-3 天把修正项落到代码 → 输出"财务级 P&L"。

## 一句话给非财务同事

> 我们当前做的是工程师视角的 P&L 草样，**财务/审计接手前必须 review 一遍**，
> 至少要拿到客户会计科目表 + 一份法定财报样本对账。在那之前，本报表标"管理会计
> ≠ 法定财报"且 Excel 顶部有 disclaimer。

## 相关文档

- [pnl-statement-design.md](./pnl-statement-design.md) — P&L 入口完整设计
- [pnl-primer-for-engineers.md](./pnl-primer-for-engineers.md) — 工程师视角财务 P&L 入门
- [architecture-evolution-roadmap.md](./architecture-evolution-roadmap.md) — 整体演进路线图（含 P0 阶段说明）
