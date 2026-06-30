# 零容差 PR-B(整数化 + 封存线 + 跨账本升级)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交易金额全管线整数化(萨当)使 sum 型金额恒等式收到 `delta == 0`;封存线机制落地;修复凭证账套餐粒度让跨账本互证升级;还清技术债 ①④⑤,推进 ②⑥⑦。

**Architecture:** 三条线——(1) 凭证账修复线:`order_line` 加 `product_type != 2` → 复跑观察 → CROSS_LEDGER 按决策树升级;(2) 整数化线:semantic 实体层金额列 `CAST(ROUND(x*100) AS INT64)` 萨当,Python 聚合保持 int,Excel 写盘 `/100`,恒等式 sum 型收零容差——**边界 = 恒等式参与的语义层管线**(手写 SQL 的 bq_exporter/standalone 报表不在内,它们走基线恒等式);(3) 封存线:`assert_month_not_frozen` 共享守卫。双跑验收用"快照前置"策略:整数化合入前对 2026-06 跑浮点引擎存快照,合入后 diff,避免养两套代码。

**Tech Stack:** 同 PR-A(venv/unittest/BQ asia-southeast1/xlsxwriter+openpyxl)。

**Spec:** `docs/superpowers/specs/2026-06-12-zero-tolerance-design.md` §6;基线:`docs/audit/2026-06-cross-ledger-baseline.md`

**前置研究结论(已实测,零上下文工程师直接用):**

- `ttpos_sale_order_product.product_type`:0=单品,1=套餐父行(`package_uuid=0`),2=套餐子行(`package_uuid`=父行 uuid)。无 NULL。`product_type != 2` 即统计账同粒度;shop005 验证 qty/gross 残差 0.00%,shop001/002 残差 0.04%/0.50%(纯 `complete_time` vs `finish_time` 月界时间语义)。
- 套餐父行可有 `sale_price=0`(如"福利套餐 2"),统计账同样记 0,两本账自洽,无需特殊处理。
- 整数化边界:**恒等式参与字段 = ttpos `decimal(12,2)` 交易金额 → 萨当 INT64 精确**;物料单价(4 位小数)、套餐权重(float)、费率、COGS/利润衍生列 = 估算量,保持 float,不参与 sum 型零容差恒等式。
- 金额列全量清单(文件:行号)见研究归档,任务里逐列列出。

---

### Task 1: order_line 套餐粒度修复

**Files:**
- Modify: `semantic/entities/order_line.py`
- Modify: `tests/test_order_line.py`

- [ ] **Step 1: 失败测试** — `tests/test_order_line.py` 加:

```python
    def test_excludes_combo_child_rows(self):
        # product_type: 0=单品, 1=套餐父行, 2=套餐子行 (package_uuid=父行uuid).
        # 子行不排除 → 凭证账 qty 翻倍 (2026-05 实测 31.5% 匹配率的根因)
        self.assertIn("sop.product_type != 2", self.sql)
```

- [ ] **Step 2:** 跑 `venv/bin/python -m unittest tests.test_order_line -v` → FAIL。

- [ ] **Step 3:** `order_line_cte()` WHERE 子句 `AND sop.delete_time = 0` 之后加一行:

```sql
    AND sop.product_type != 2
```

并在 docstring 口径要点追加:

```
  - sop.product_type != 2 排除套餐子行 (2=子行, package_uuid 指向父行 uuid):
    统计账记 SKU 粒度, 凭证账不排子行会按套餐组件翻倍
    (2026-05 基线 31.5% 匹配率的根因, shop005 实测排除后残差 0.00%).
```

- [ ] **Step 4:** 跑测试 PASS + 全量回归(468 OK)。Commit:

```bash
git add semantic/entities/order_line.py tests/test_order_line.py
git commit -m "fix(semantic): order_line 排除套餐子行 — 凭证账对齐统计账 SKU 粒度"
```

---

### Task 2: 观察复跑 + 三项归因 + CROSS_LEDGER 升级决策

**Files:**
- Modify: `scripts/adhoc/audit_cross_ledger_202605.py`(如需,仅输出增强)
- Modify: `docs/audit/2026-06-cross-ledger-baseline.md`(追加"复跑"章节)
- Create: `scripts/adhoc/audit_tieout_outliers_202605.py`(2 单归因 + 支付口径探查)
- Modify: `semantic/validators/identities.py`(按决策树执行升级)
- 可能 Modify: `bq_reports/profit_by_price_report.py` 等(若升级进闸门)

- [ ] **Step 1: 复跑观察** — `venv/bin/python scripts/adhoc/audit_cross_ledger_202605.py 2>&1 | tee /tmp/cross_ledger_rerun.txt`。预期:qty 匹配率从 31.5% → ≥99%(残差集中在月界订单)。

- [ ] **Step 2: 归因探查脚本** — `audit_tieout_outliers_202605.py` 干三件事(read-only):
  1. 拉出店006 order=373316429388、店059 order=372817075896 的订单 + 全部 item 行 + 同单退款/修正记录,归因 +99/+89 THB(技术债 ⑦)
  2. 支付勾稽口径探查(技术债 ②):任选 3 家店,按 bill 粒度算 `payment_amount` vs `amount` vs `product_amount` vs `service_fee` vs `tax_fee` vs 会员储值支付(`payment_order` 的支付方式拆分,见 docs/bq-schema-reference.md 订单体系),输出"30-50% 差额的构成分解表"——目标是给出 `payment_amount ≈ stat_actual − X − Y` 的候选公式
  3. qty 复跑残差抽样:取 5 个仍不匹配的 (store,item),拉两边明细对比时间字段,确认残差 = 月界时间语义(或发现新东西)

- [ ] **Step 3: 更新基线文档** — 追加章节"## 复跑 (PR-B Task 2)":复跑数字、2 单归因结论、支付差额构成分解、残差性质确认。

- [ ] **Step 4: 按决策树执行升级**(写死在文档里,按实测勾选):
  - **(a) qty 匹配率 = 100%** → `CROSS_LEDGER_QTY` 保持零容差 classify,`CROSS_LEDGER_IDENTITIES` 进 `FULL_IDENTITIES`;给 sale_event 系报表(profit_by_price)喂 cross_ledger rows(order_line 查询 + build_cross_ledger_rows,gate 第二道)
  - **(b) ≥99% 且残差全部归因月界时间语义** → CROSS_LEDGER 以**封顶 🟡** 进 `FULL_IDENTITIES`(新 classify:`_capped_review_money` 同款思路,qty 用 `_capped_review_qty`——非零即 🟡),每次导出可见但不阻断;零容差升级条件写进技术债 ⑥:时间语义对齐方案定稿后
  - **(c) <99% 或出现不可归因残差** → 维持观察模式,基线文档记录新根因,技术债 ⑥ 更新
  按所选分支落代码(b 分支需在 identities.py 加 `_capped_review_qty` + 调整 bundle;a/b 都要在 profit_by_price 报表加第二道 gate 调用——SQL 双查 stat+voucher,build_cross_ledger_rows,`validate_and_gate(rows, CROSS_LEDGER_IDENTITIES, ...)`)。

- [ ] **Step 5:** 全量回归 + Commit:

```bash
git add scripts/adhoc/ docs/audit/ semantic/ bq_reports/
git commit -m "chore(audit): 跨账本复跑 — qty <实测>%; 2单归因+支付口径分解; CROSS_LEDGER <决策>"
```

---

### Task 3: 封存线 month guard(技术债 ①)

**Files:**
- Modify: `semantic/dimensions/time.py`
- Create: `tests/test_month_guard.py`
- Modify: 所有带 `--month`/月份参数的报表脚本(engine 系 4 + exporter 系 5 + standalone 销售系 3;菜单/BOM 导出无月份参数,不适用)
- Modify: `CLAUDE.md`(新增"历史封存"节 + 技术债 ① 落表)

- [ ] **Step 1: 失败测试** — `tests/test_month_guard.py`:

```python
"""封存线: 2026-05 及之前为旧浮点口径封存月, 交付物禁止重新导出 (spec §2 决策 2/3)。"""
import unittest

import tests._setup  # noqa: F401

from semantic.dimensions.time import FROZEN_BEFORE_MONTH, assert_month_not_frozen


class TestMonthGuard(unittest.TestCase):
    def test_frozen_month_blocks(self):
        with self.assertRaises(SystemExit) as cm:
            assert_month_not_frozen("2026-05")
        self.assertEqual(cm.exception.code, 3)

    def test_old_month_blocks(self):
        with self.assertRaises(SystemExit):
            assert_month_not_frozen("2025-12")

    def test_current_month_passes(self):
        assert_month_not_frozen("2026-06")  # 不抛即过

    def test_future_month_passes(self):
        assert_month_not_frozen("2027-01")

    def test_constant_value(self):
        self.assertEqual(FROZEN_BEFORE_MONTH, "2026-06")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2:** FAIL 后在 `semantic/dimensions/time.py` 加:

```python
# 封存线 (spec 决策 2/3): 该月之前的交付物 = 旧浮点口径, 永不重算.
# 只读对账/审计查询不受限 (观察跑用 BQ 查询, 不走报表导出入口).
FROZEN_BEFORE_MONTH = "2026-06"


def assert_month_not_frozen(month: str) -> None:
    """月份封存守卫 — 报表导出入口必调 (YYYY-MM 字符串字典序可直接比较).

    封存月直接 exit 3 (区别于闸门的 exit 2), 提示去 exports/ 找已交付归档.
    """
    if month < FROZEN_BEFORE_MONTH:
        print(f"🧊 {month} 已封存 (封存线 {FROZEN_BEFORE_MONTH}, 旧浮点口径, 永不重算).")
        print("   已交付文件在 exports/ 归档; 如需对账/审计请用只读查询 (scripts/adhoc/).")
        print("   口径说明: CLAUDE.md「历史封存」节 + docs/superpowers/specs/2026-06-12-zero-tolerance-design.md §6.")
        raise SystemExit(3)
```

- [ ] **Step 3: 接入 12 个带月份参数的脚本** — 每个脚本在解析出 month 后立即调用 `assert_month_not_frozen(args.month)`(2 个 sys.argv 脚本在取 `sys.argv[1]` 后调)。逐个确认参数名(有的叫 `--month`,period 类可能是起止日期——起止日期类脚本守卫规则:**窗口起点所在月 < 封存线即拒**,换算后调用)。

- [ ] **Step 4: CLAUDE.md** — 新增节(放在技术债节之前):

```markdown
### 5.5 历史封存(零容差口径切换线)

**封存线 = 2026-06**。2026-05 及之前的月份为旧浮点口径交付物,**永不重算**
(`semantic/dimensions/time.py: assert_month_not_frozen`,报表入口拒跑 exit 3)。
新旧口径数字不可逐分比较;只读对账/审计查询不受限。
依据:spec 决策 2/3(`docs/superpowers/specs/2026-06-12-zero-tolerance-design.md`)。
```

技术债表:① 行改为已落地状态(守卫文件:行号),不再是"待 PR-B"。

- [ ] **Step 5:** 测试全绿 + py_compile 12 脚本 + Commit:

```bash
git add semantic/dimensions/time.py tests/test_month_guard.py bq_reports/ CLAUDE.md
git commit -m "feat(guard): 封存线 month guard — 2026-05 及之前拒绝重新导出 (exit 3), 技术债①落地"
```

---

### Task 4: sale_line/takeout_line 投影 gross_amount(技术债 ④ 前半)

**Files:**
- Modify: `semantic/entities/sale_line.py`、`semantic/entities/takeout_line.py`
- Modify: `bq_reports/profit_margin_report.py`、`bq_reports/report_sales_period_bq.py`(删定义式补齐,改读真列)
- Test: `tests/test_sale_line.py` 类比 sale_event 模式加断言(若无此测试文件则建)

- [ ] **Step 1: 失败测试**(断言与 sale_event 同式):sale_line 加 `SUM(sp.product_sale_price * sp.product_num) AS gross_amount`;takeout_line 加 `SUM(toi.price * toi.quantity) AS gross_amount`(不分 state,与 sale_event 外卖支同式)。

- [ ] **Step 2:** 实现两个 CTE 列(位置:各自 `sales_price` 之后,注释同 sale_event)。**takeout_line 的金额恒等式平凡性此时部分解除**:gross 全量 vs sales_price(10-40)+cancelled(60) 在 takeout_line 粒度也成立,守恒闭环延伸到旧粒度报表。

- [ ] **Step 3:** `profit_margin_report.py`:删掉 `# 定义式补齐...` 两处(累加 :1027 与 result dict 已有 key 保留,但累加源改为 `float(getattr(row, "gross_amount", 0) or 0)`,与 revenue 同模式);`report_sales_period_bq.py` 同理(其 SQL 投影 gross_amount 后改读真列)。**改完后这两个报表的毛额守恒恒等式才是真校验**——在 commit message 里写明。

- [ ] **Step 4:** 全量回归 + 2026-06 冒烟 profit_margin(预期:毛额守恒在真数据上 ✅ 或暴露真 delta——若有 delta 如实记录,不许调阈值);Commit:

```bash
git commit -m "feat(semantic): sale_line/takeout_line 投影 gross_amount — profit_margin/sales_period 毛额守恒转真校验 (技术债④前半)"
```

---

### Task 5: pnl_statement 字段对齐(技术债 ⑤)

**Files:**
- Modify: `bq_reports/pnl_statement.py`

- [ ] **Step 1:** 读 pnl_statement 的 sales 行装配(`_build_pnl_sales_sql` 拉 sale_event 全字段,含 Task 9/PR-A 加的 gross_amount)。按店聚合构造 check_rows:store 粒度 SUM 各桶(qty/free/give/refund/cancelled + sales_price/actual(→revenue)/refund/free/give/discount/cancelled_amount/gross_amount),`net_qty` 仍为定义式(SALES_QTY 是定义式守卫,如实)。

- [ ] **Step 2:** gate 调用从 `identities=[]` 升级为 `DEFAULT_IDENTITIES`(店粒度行),删"技术债 ⑤"注释,换成"店粒度销售恒等式;P&L 层金额(COGS/费用)是估算量不参与"。

- [ ] **Step 3:** 全量回归 + 真 BQ 冒烟 pnl_statement 2026-06(`--force` 跑通看校验段输出,删产物);Commit:

```bash
git commit -m "feat(reports): pnl_statement 店粒度接销售恒等式 — 技术债⑤还清"
```

---

### Task 6: 双跑快照(整数化之前,先存浮点引擎基准)

**Files:**
- Create: `scripts/adhoc/dump_float_baseline_202606.py`

- [ ] **Step 1:** 写快照脚本:对 2026-06,用**当前(浮点)引擎**跑 profit_margin 与 profit_by_price 的聚合管线(不写 Excel,只到 check_rows/agg 层),把每行 (store, item, 全部金额指标) dump 成 JSON:`exports/.dual_run/float_baseline_202606.json`(目录 gitignore 不入库;同时把行数与各指标全局 SUM 打印进 console 留档)。

- [ ] **Step 2:** 跑它,确认 JSON 生成 + console 汇总合理(行数与冒烟时一致量级)。Commit(只 commit 脚本):

```bash
git commit -m "chore(audit): 双跑快照脚本 — 整数化前浮点基准 dump (2026-06)"
```

**⚠️ 顺序约束:本任务必须在 Task 7 之前完成并实际跑出 JSON,否则双跑验收无对照。**

---

### Task 7: 整数化(萨当)— 三步走

**边界(写进每个 commit message):恒等式参与的交易金额列整数化;物料单价(4dp)/权重/费率/COGS/利润衍生列保持 float 估算。**

#### 7a: SQL 实体层

**Files:** `semantic/entities/sale_event.py`、`sale_line.py`、`takeout_line.py`、`total_line.py`、`order_line.py`、`takeout_tieout.py` + 对应 render 测试

- [ ] 逐列(研究清单):sale_event 堂食 `sales_price/gross_amount/original_amount/actual_amount/refund_amount/free_amount/give_amount/discount_amount`、外卖 `sales_price/gross_amount/original_amount/actual_amount/cancelled_amount`;sale_line/takeout_line 同名列;total_line 的 IFNULL 相加列(输入已是萨当,无需再转);order_line `voucher_gross/voucher_net/voucher_discount`;takeout_tieout `platform_total/merchant_charge_fee/merchant_discount/item_sum`。模式:

```sql
    CAST(ROUND(SUM(sp.product_sale_price * sp.product_num) * 100) AS INT64) AS sales_price,
```

(在 SUM 外层 ×100 ROUND CAST——源字段 2dp,SUM 后 ×100 至多半萨当浮点表示误差,ROUND 吃掉;**唯一舍入点 = CTE 输出层**,docstring 声明"单位: 萨当 (satang), INT64")。`avg_member_discount`(比率)不动。

- [ ] 每个实体的 render 测试同步更新断言;全量回归会暴露依赖数值 fixture 的测试(单位换挡 ×100)——**fixture 数值统一 ×100 并在文件头注释"金额单位: 萨当"**,不许改断言逻辑。

#### 7b: Python 聚合 + Excel 写盘

**Files:** `bq_reports/profit_margin_report.py`、`profit_by_price_report.py`、`report_sales_period_bq.py`、`pnl_statement.py`、`utils/report_engine.py`、`resources/reports/*.yaml`

- [ ] 聚合层:BQ INT64 → Python int,`float(...)` 包裹改 `int(... or 0)`(只对交易金额列;qty 本就 int);`round(x, 2)` 对萨当列删除(int 无需 round)。
- [ ] Excel 写盘:`ColumnConfig` 加 `money_satang: bool = False`(YAML 列配置 `money_satang: true`),`write_configured_sheet` 对该列写 `value / 100`;不走 engine 的 sales_period/pnl 在写盘处显式 `/100`。**COGS/利润/费用列不标记**(它们是 float 估算,直接写)。利润计算处(`revenue - total_cost`)注意单位:revenue 现为萨当 → 参与 float 衍生计算前 `revenue / 100`(集中在一处换算并注释"萨当→元, 进入估算域")。
- [ ] 真 BQ 冒烟 profit_margin 2026-06 `--force`,人工核对 Excel 金额量级正确(没有 100 倍错位),核对后删产物。

#### 7c: 恒等式收零 + 扰动测试收紧

**Files:** `semantic/validators/identities.py`、`tests/test_identity_perturbation.py`、`tests/test_gate.py` 等 fixture

- [ ] sum 型恒等式(AMOUNT、GROSS_AMOUNT、CROSS_LEDGER_GROSS 若 Task 2 已进 bundle 则视其残差性质决定)classify 换为:

```python
def _exact_satang_classify(delta: float, lhs: float) -> Severity:
    """整数萨当算术精确 — 零是唯一可接受答案 (spec §6 B 终态前置)."""
    return Severity.NEGLIGIBLE if delta == 0 else Severity.MUST_FIX
```

AMOUNT_IDENTITY/GROSS_AMOUNT_IDENTITY 的 classify 指向它。`_money_classify` 保留给仍带容忍带的(跨账本 gross 若残差源于两本账各自数据而非浮点、支付/外卖勾稽封顶类),其常量换算萨当单位:`_NEGLIGIBLE_ABS=1`(1 萨当)、`_NEGLIGIBLE_ABS_LOOSE=100`(1 THB)、`_MUST_FIX_ABS=10_000`(100 THB),注释标明单位。
- [ ] 扰动测试:`MONEY_DELTA = 1.0`(1 萨当即 fire——零容差的证明);`_passing_sales_row` 金额 fixture ×100;测试文件头注释更新。
- [ ] 全量回归;2026-06 真数据冒烟:金额恒等式在真数据上 delta==0 全绿(若有非零 delta → 如实报告,排查是管线还是源数据,**不许加容忍**)。

- [ ] Commits(三个):

```bash
git commit -m "feat(semantic): 实体层交易金额整数化萨当 INT64 — 唯一舍入点在 CTE 输出 (7a)"
git commit -m "feat(reports): 聚合 int 化 + Excel 萨当/100 写盘 — 估算域(COGS/费率)保持 float (7b)"
git commit -m "feat(validators): sum 型金额恒等式收 delta==0 — 扰动测试 1 萨当即 fire (7c)"
```

---

### Task 8: 双跑 diff 验收

**Files:**
- Create: `scripts/adhoc/diff_dual_run_202606.py`
- Create: `docs/audit/2026-06-dual-run-integerization.md`

- [ ] **Step 1:** diff 脚本:用整数引擎重跑 Task 6 同范围聚合 → 与 `float_baseline_202606.json` 逐 (store, item, 指标) 对比(萨当 `/100` 后比),容忍 |diff| ≤ 0.01 THB(浮点基准本身的精度);超限逐行列出。
- [ ] **Step 2:** 跑,产出归档 `docs/audit/2026-06-dual-run-integerization.md`(汇总:对比行数、全配对数、超限清单或"零超限"、结论)。若有超限:逐条归因(浮点基准累加误差 vs 整数化引入错误),整数化错误必须修复后复跑,**不许带超限验收**。
- [ ] **Step 3:** 浮点路径清理:整数化是就地修改,无双代码路径;在 diff 报告写明"spec §6 '删除旧浮点路径' 以快照前置策略满足——无并行引擎存在"。删除 `exports/.dual_run/` 本地产物说明保留 JSON 至 PR-B 合入(本地,不入库)。
- [ ] Commit:

```bash
git add scripts/adhoc/diff_dual_run_202606.py docs/audit/2026-06-dual-run-integerization.md
git commit -m "chore(audit): 双跑 diff 验收 — 浮点基准 vs 整数引擎 (2026-06), <结论>"
```

---

### Task 9: 文档收口

**Files:** `CLAUDE.md`、`docs/superpowers/specs/2026-06-12-zero-tolerance-design.md`、`docs/audit/2026-06-cross-ledger-baseline.md`

- [ ] CLAUDE.md 技术债表全面更新:① 已落地(Task 3)、④ 前半还清(Task 4;双轨合并仍留)、⑤ 还清(Task 5)、⑥ 按 Task 2 决策更新、⑦ 按归因结论更新或销账、② 按支付分解结论更新(给出候选公式或"待业务确认"现状)。新增(若有):整数化边界声明——"零容差数学保证覆盖语义层管线;手写 SQL 报表(bq_exporter/standalone)走基线恒等式,金额仍 float"。
- [ ] spec §6 追加"PR-B 实施记录"块(同 §5 勘误模式):快照前置双跑、整数化边界、CROSS_LEDGER 实际升级档位。
- [ ] 全量回归 + Commit:

```bash
git commit -m "docs: 零容差 PR-B 收口 — 技术债①④⑤清账, ②⑥⑦推进, 整数化边界声明"
```

---

## PR-B 完成定义

- [ ] 凭证账套餐粒度修复,复跑 qty 匹配率实测并归档;CROSS_LEDGER 升级决策(a/b/c)落地且有记录
- [ ] month guard 生效:封存月 exit 3;CLAUDE.md 历史封存节 + 技术债 ① 落表
- [ ] sum 型金额恒等式 `delta == 0`,扰动测试 1 萨当即 fire,2026-06 真数据全绿(或暴露的真 delta 已归因修复)
- [ ] 双跑 diff 归档,零超限(或超限全部归因为浮点基准误差)
- [ ] 技术债 ④(前半)⑤ 还清;②⑥⑦ 状态如实更新
- [ ] `venv/bin/python -m unittest discover tests` 全绿

## 不在 PR-B(防 scope creep)

- 尾差记账 `allocations.py` + `rounding_residual` 桶 + `_money_classify` 彻底删除(PR-C)
- sale_event/sale_line 双轨合并(技术债 ④ 后半,等 CROSS_LEDGER 零容差达成后)
- 手写 SQL 报表(bq_exporter/standalone)的金额整数化(边界外,基线恒等式已覆盖)
- 对账桥产品化/口径注册表(子项目 D)
