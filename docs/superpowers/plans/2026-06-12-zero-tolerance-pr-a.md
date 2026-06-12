# 零容差 PR-A(让恒等式为真)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让校验体系可证伪——凭证账互证、守恒闭环、导出闸门、19 个报表脚本全覆盖,任何无声错误要么不存在要么显式可见。

**Architecture:** 新增凭证账 CTE(`order_line`)与跨账本恒等式实现独立来源互证;新增导出闸门 `gate.py`(硬阻断 + `--force` 水印)作为全部脚本的统一收口;AST 结构性测试用"收缩名单"驱动 19 个脚本逐批接入;2026-05 观察跑产出跨账本差异底数,决定跨账本恒等式是否进阻断名单。

**Tech Stack:** Python 3 (venv)、BigQuery SQL CTE 工厂模式、stdlib unittest、xlsxwriter/openpyxl 双写盘栈。

**Spec:** `docs/superpowers/specs/2026-06-12-zero-tolerance-design.md`(§5 子项目 A)

**关键背景(零上下文工程师必读):**

- 所有 Python 用 `venv/bin/python`,测试跑 `venv/bin/python -m unittest discover tests`(stdlib unittest,无 pytest)
- 每个测试模块第一行 import `tests._setup`(把 repo root 放进 sys.path)
- SQL CTE 工厂模式:`semantic/entities/*.py` 的函数返回 `"name AS (...)"` 字符串,占位符 `{project}`/`{dataset}`/`{start_ts}`/`{end_ts}`,调用方拼 WITH 子句
- ttpos 软删约定:`delete_time = 0` 表示未删除(不是 NULL)
- 19 个报表脚本分三类写盘:3 个走 `utils/report_engine.py`(xlsxwriter)、5 个走 `bq_reports/utils/bq_exporter.py`、11 个各自裸写 openpyxl/xlsxwriter——**没有统一 chokepoint,闸门必须是独立函数**
- **与 spec 的一处实现偏差(有意为之,Task 6 落档):**spec §5 A2 说"sale_event 接入 merchant_charge_fee/merchant_discount"。这两个字段在 `ttpos_takeout_order` 是**订单级**的,JOIN 到 item 行再 SUM 会按订单内 item 数重复计数。正确归宿是订单粒度的 `takeout_tieout` CTE(Task 6),sale_event 只加 item 粒度可加的 `gross_amount`(Task 3)。完成后在 spec 加一行勘误注记。

---

### Task 1: Identity.fields 元数据 + 扰动测试框架 + SALES_QTY 特征化测试

恒等式的 lambda 无法内省引用了哪些字段,先给 `Identity` 加 `fields` 元数据,扰动测试据此逐字段扰动。同时用特征化测试钉死"SALES_QTY 在减法推导的 row 上永真"这一已知局限。

**Files:**
- Modify: `semantic/validators/core.py:33-46`(Identity dataclass)
- Modify: `semantic/validators/identities.py:56-77`(补 fields 元数据)
- Create: `tests/test_identity_perturbation.py`

- [ ] **Step 1: 写失败测试**

```python
"""扰动测试:每条恒等式对它引用的每个字段必须敏感(可证伪性的机制保障)。

金额扰动用 +200(超过 _MUST_FIX_ABS=100),因为 A 阶段金额仍有容忍带;
PR-B 整数化后收紧为 +1 萨当。qty 扰动 +1(qty 零容差,立即可测)。
"""
import unittest

import tests._setup  # noqa: F401

from semantic.validators import check
from semantic.validators.core import Severity
from semantic.validators.identities import SALES_QTY_IDENTITY, AMOUNT_IDENTITY


def _passing_sales_row():
    """一行满足全部销售恒等式的平衡数据。"""
    return {
        "qty": 100.0, "net_qty": 80.0, "free_qty": 5.0, "give_qty": 5.0,
        "refund_qty": 6.0, "cancelled_qty": 4.0,
        "sales_price": 1000.0, "revenue": 800.0, "refund_amount": 60.0,
        "free_amount": 50.0, "give_amount": 50.0, "discount_amount": 40.0,
        "cancelled_amount": 30.0,
    }


QTY_DELTA = 1.0
MONEY_DELTA = 200.0  # > _MUST_FIX_ABS, 保证穿透 A 阶段容忍带
MONEY_FIELDS = {
    "sales_price", "revenue", "refund_amount", "free_amount",
    "give_amount", "discount_amount", "cancelled_amount", "gross_amount",
}


class TestIdentityPerturbation(unittest.TestCase):
    PERTURBABLE = [SALES_QTY_IDENTITY, AMOUNT_IDENTITY]

    def test_identities_declare_fields(self):
        for ident in self.PERTURBABLE:
            self.assertTrue(ident.fields,
                            f"{ident.name} 缺 fields 元数据, 扰动测试无法覆盖它")

    def test_base_row_passes(self):
        result = check([_passing_sales_row()], self.PERTURBABLE)
        self.assertEqual(result.violations, [],
                         f"基准行必须全绿: {[(v.identity.name, v.delta) for v in result.violations]}")

    def test_every_field_perturbation_fires(self):
        for ident in self.PERTURBABLE:
            for f in ident.fields:
                row = _passing_sales_row()
                row[f] += MONEY_DELTA if f in MONEY_FIELDS else QTY_DELTA
                result = check([row], [ident])
                self.assertTrue(
                    result.violations,
                    f"{ident.name} 对字段 {f} 的扰动不敏感 — 恒等式不可证伪")


class TestSalesQtyIsDefinitional(unittest.TestCase):
    def test_derived_net_qty_makes_identity_vacuous(self):
        """特征化测试:net_qty 用减法推导时, 即使源数据离谱, SALES_QTY 照样通过.

        这就是 spec §1 问题 1 (循环恒等式). 真实检测力由 CROSS_LEDGER 提供 (Task 5).
        本测试存在的意义: 防止后人误信 SALES_QTY 有检测力 / 防止报表偷偷回到减法推导
        还宣称"过了校验".
        """
        raw = {"qty": 100.0, "free_qty": 5.0, "give_qty": 5.0,
               "refund_qty": 999.0, "cancelled_qty": 4.0}  # refund 离谱地错
        row = {
            **raw,
            "net_qty": raw["qty"] - raw["free_qty"] - raw["give_qty"]
                       - raw["refund_qty"] - raw["cancelled_qty"],
        }
        result = check([row], [SALES_QTY_IDENTITY])
        self.assertEqual(result.violations, [])  # 永真 — 文档化这个事实


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m unittest tests.test_identity_perturbation -v`
Expected: `test_identities_declare_fields` FAIL(`Identity` 无 `fields` 属性 → AttributeError 或空)

- [ ] **Step 3: 给 Identity 加 fields,给两条恒等式补元数据**

`semantic/validators/core.py` Identity dataclass 末尾加一个字段:

```python
@dataclass(frozen=True)
class Identity:
    """One reconciliation rule: lhs(row) should equal rhs(row). ..."""

    name: str
    lhs: Callable[[dict], float]
    rhs: Callable[[dict], float]
    classify: Callable[[float, float], Severity]
    description: str = ""
    # 该恒等式读取的 row 字段清单 — 扰动测试 (tests/test_identity_perturbation)
    # 据此逐字段扰动验证可证伪性. lambda 无法内省, 故显式声明.
    fields: tuple = ()
```

`semantic/validators/identities.py` 两条恒等式各加 `fields=`:

```python
SALES_QTY_IDENTITY = Identity(
    name="销量恒等式",
    description="qty = net_qty + free_qty + give_qty + refund_qty + cancelled_qty",
    lhs=lambda r: r["qty"],
    rhs=lambda r: (r["net_qty"] + r["free_qty"] + r["give_qty"]
                   + r["refund_qty"] + r["cancelled_qty"]),
    classify=_qty_classify,
    fields=("qty", "net_qty", "free_qty", "give_qty", "refund_qty", "cancelled_qty"),
)
```

```python
AMOUNT_IDENTITY = Identity(
    name="金额恒等式",
    description=(
        "sales_price = revenue + refund + free + give + discount\n"
        "（cancelled_amount 不参与：ttpos sales_price 已按 state=60 排除，"
        "取消件价格由 GROSS_AMOUNT_IDENTITY 闭环审计）"
    ),
    lhs=lambda r: r["sales_price"],
    rhs=lambda r: (r["revenue"] + r["refund_amount"]
                   + r["free_amount"] + r["give_amount"]
                   + r["discount_amount"]),
    classify=_money_classify,
    fields=("sales_price", "revenue", "refund_amount", "free_amount",
            "give_amount", "discount_amount"),
)
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `venv/bin/python -m unittest tests.test_identity_perturbation -v && venv/bin/python -m unittest discover tests 2>&1 | tail -3`
Expected: 全部 PASS / OK

- [ ] **Step 5: 给 SALES_QTY 加降级备注(spec §10 要求的代码备注)**

`identities.py` 中 `SALES_QTY_IDENTITY` 定义上方加注释:

```python
# ⚠️ 定义式守卫, 非对账. 报表层的 net_qty 是 `qty − 其他桶` 减法推导的,
# 本恒等式在这种 row 上代数永真 (见 tests/test_identity_perturbation.py
# TestSalesQtyIsDefinitional). 它的价值仅剩: 防字段缺失 / schema 漂移.
# 真实检测力 = CROSS_LEDGER_IDENTITIES (统计账 vs 凭证账互证).
```

- [ ] **Step 6: Commit**

```bash
git add semantic/validators/core.py semantic/validators/identities.py tests/test_identity_perturbation.py
git commit -m "test(validators): Identity.fields 元数据 + 扰动测试框架; SALES_QTY 降级为定义式守卫"
```

---

### Task 2: 修 reconciliation base=0 漏洞

`classify_money_severity` 在 base=0 时 rel=0.0,`abs_d < negligible_abs or rel < negligible_rel` 的 or 分支让任意大差额被判 NEGLIGIBLE。

**Files:**
- Modify: `semantic/reconciliation/base.py:112-136`
- Create: `tests/test_reconciliation_classify.py`

- [ ] **Step 1: 写失败测试**

```python
"""classify_money_severity 的边界测试 — 重点钉死 base=0 漏洞 (spec §1 问题 5)。"""
import unittest

import tests._setup  # noqa: F401

from semantic.reconciliation.base import (
    ReconciliationSeverity,
    classify_money_severity,
)


class TestClassifyMoneySeverity(unittest.TestCase):
    def test_zero_base_large_delta_must_fix(self):
        """external=0 但 BQ 侧 10000 — 修复前被 or 分支放过成 NEGLIGIBLE."""
        sev = classify_money_severity(10000.0, 0.0)
        self.assertEqual(sev, ReconciliationSeverity.MUST_FIX)

    def test_zero_base_tiny_delta_negligible(self):
        sev = classify_money_severity(0.5, 0.0)
        self.assertEqual(sev, ReconciliationSeverity.NEGLIGIBLE)

    def test_small_abs_and_small_rel_negligible(self):
        # abs<1 且 rel 极小 → NEGLIGIBLE (合法的累积舍入)
        sev = classify_money_severity(0.5, 1_000_000.0)
        self.assertEqual(sev, ReconciliationSeverity.NEGLIGIBLE)

    def test_small_rel_but_large_abs_not_negligible(self):
        """rel<0.0001 但绝对差 500 元 — 修复前被 rel 分支放过."""
        sev = classify_money_severity(500.0, 10_000_000.0)
        self.assertNotEqual(sev, ReconciliationSeverity.NEGLIGIBLE)

    def test_fatal_rel(self):
        sev = classify_money_severity(200.0, 1000.0)  # rel=0.2 > 0.01
        self.assertEqual(sev, ReconciliationSeverity.MUST_FIX)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m unittest tests.test_reconciliation_classify -v`
Expected: `test_zero_base_large_delta_must_fix` 和 `test_small_rel_but_large_abs_not_negligible` FAIL

- [ ] **Step 3: 修复实现**

`semantic/reconciliation/base.py` 替换 `classify_money_severity` 函数体(签名与默认参数不变):

```python
    """金额对账的通用 severity 分类.

    NEGLIGIBLE:   abs < negligible_abs (绝对小, 无条件放过)
                  或 (abs < review_abs 且 rel < negligible_rel) (相对极小且绝对不大)
    MUST_FIX:     rel > fatal_rel, 或 base=0 且 abs >= review_abs (无基数可比的大差额)
    NEEDS_REVIEW: 其它超过 review 线的

    修复记录: 旧版 `abs_d < negligible_abs or rel < negligible_rel` 在 base=0 时
    rel=0.0 恒小于阈值, 任意大差额被放过 (spec §1 问题 5). rel 现在只在
    base != 0 时有意义; base=0 走纯绝对值判断.
    """
    abs_d = abs(abs_delta)
    if abs_d < negligible_abs:
        return ReconciliationSeverity.NEGLIGIBLE
    if not base:
        # 无基数: 只能按绝对值判, 大差额必查
        return (ReconciliationSeverity.MUST_FIX if abs_d >= review_abs
                else ReconciliationSeverity.NEEDS_REVIEW)
    rel = abs_d / abs(base)
    if abs_d < review_abs and rel < negligible_rel:
        return ReconciliationSeverity.NEGLIGIBLE
    if rel > fatal_rel:
        return ReconciliationSeverity.MUST_FIX
    if abs_d > review_abs or rel > review_rel:
        return ReconciliationSeverity.NEEDS_REVIEW
    return ReconciliationSeverity.NEGLIGIBLE
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `venv/bin/python -m unittest tests.test_reconciliation_classify -v && venv/bin/python -m unittest discover tests 2>&1 | tail -3`
Expected: 全部 PASS / OK(若 `tests/test_platform_payout.py` 等存量测试依赖旧行为而挂,逐个看:依赖"base=0 放过"的断言是在依赖 bug,改断言并在 commit message 说明)

- [ ] **Step 5: Commit**

```bash
git add semantic/reconciliation/base.py tests/test_reconciliation_classify.py
git commit -m "fix(reconciliation): classify_money_severity base=0 时任意大差额被判 NEGLIGIBLE"
```

---

### Task 3: sale_event 加 gross_amount 列 + GROSS_AMOUNT 恒等式(守恒闭环)

外卖侧 `gross_amount = SUM(price×qty) 不分 state` 是真正非平凡的:若 ttpos 新增 order_state(如 50),金额会从 sales_price(只算 10-40)和 cancelled_amount(只算 60)之间漏掉,本恒等式立刻 fire。堂食无 state 概念,gross == sales_price 平凡成立但无害。

**Files:**
- Modify: `semantic/entities/sale_event.py`(两个分支各加一列)
- Modify: `semantic/validators/identities.py`(新恒等式 + 进 DEFAULT_IDENTITIES)
- Modify: `tests/test_identity_perturbation.py`(PERTURBABLE 加新恒等式)
- Test: `tests/test_sale_event.py`(SQL 渲染断言)

- [ ] **Step 1: 写失败的 SQL 渲染测试**

`tests/test_sale_event.py` 的 TestCase 里加(沿用该文件现有 `self.sql` setUp 模式):

```python
    def test_gross_amount_dine(self):
        # 堂食: gross == 标价×销量 (无 state 概念, 与 sales_price 同式)
        self.assertIn(
            "SUM(sp.product_sale_price * sp.product_num) AS gross_amount", self.sql)

    def test_gross_amount_takeout_unconditioned(self):
        # 外卖: 不分 state 全量 — 这是守恒闭环的关键, 不许加 IF(order_state ...)
        self.assertIn("SUM(toi.price * toi.quantity) AS gross_amount", self.sql)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m unittest tests.test_sale_event -v`
Expected: 两条新断言 FAIL

- [ ] **Step 3: 改 sale_event.py**

堂食分支(现有 `SUM(sp.product_sale_price * sp.product_num) AS sales_price,` 之后)加:

```sql
    -- 毛额 (守恒闭环锚): 堂食无 state, 与 sales_price 同式
    SUM(sp.product_sale_price * sp.product_num) AS gross_amount,
```

外卖分支(现有 `SUM(IF(t.order_state IN (10,20,30,40), toi.price * toi.quantity, 0)) AS sales_price,` 之后)加:

```sql
    -- 毛额: 不分 state 全量. GROSS_AMOUNT 恒等式据此审计 state 枚举完备性 —
    -- 若 ttpos 新增 state, 金额从 sales_price/cancelled 之间漏掉, 恒等式立刻 fire
    SUM(toi.price * toi.quantity) AS gross_amount,
```

- [ ] **Step 4: 跑 SQL 测试通过,写失败的恒等式测试**

Run: `venv/bin/python -m unittest tests.test_sale_event -v` → PASS

`tests/test_identity_perturbation.py` 顶部 import 加 `GROSS_AMOUNT_IDENTITY`,`_passing_sales_row()` 加 `"gross_amount": 1030.0,`(= sales_price 1000 + cancelled 30),`PERTURBABLE` 列表加 `GROSS_AMOUNT_IDENTITY`。

Run: `venv/bin/python -m unittest tests.test_identity_perturbation -v`
Expected: FAIL(import error:GROSS_AMOUNT_IDENTITY 不存在)

- [ ] **Step 5: 实现 GROSS_AMOUNT_IDENTITY**

`semantic/validators/identities.py` 在 AMOUNT_IDENTITY 之后加:

```python
GROSS_AMOUNT_IDENTITY = Identity(
    name="毛额守恒恒等式",
    description=(
        "gross_amount = sales_price + cancelled_amount\n"
        "守恒闭环: 把被金额恒等式排除的取消金额纳入审计 (spec §5 A3). 外卖侧"
        " gross 不分 state 全量, 本式审计 state 枚举完备性 — ttpos 若新增 state,"
        " 金额漏桶立刻 fire."
    ),
    lhs=lambda r: r["gross_amount"],
    rhs=lambda r: r["sales_price"] + r["cancelled_amount"],
    classify=_money_classify,
    fields=("gross_amount", "sales_price", "cancelled_amount"),
)
```

`DEFAULT_IDENTITIES` 改为:

```python
DEFAULT_IDENTITIES = [SALES_QTY_IDENTITY, AMOUNT_IDENTITY, GROSS_AMOUNT_IDENTITY]
```

- [ ] **Step 6: 全量回归(预期有连带修复)**

Run: `venv/bin/python -m unittest discover tests 2>&1 | tail -5`

预期挂两类,都按此修:

1. `tests/_setup.py` 的 `order_row()` defaults 加 `gross_amount=0,`
2. 已接校验的报表聚合若挂(check_rows 缺 gross_amount 字段被 core.check 记 MUST_FIX):在 Task 9 接闸门时一并把 `gross_amount` 加进各报表的 METRIC keys;本 task 只需测试套件全绿。若 `tests/test_p2_identities.py` 等用了完整 row fixture,给 fixture 补 `gross_amount` 字段

Expected: OK

- [ ] **Step 7: Commit**

```bash
git add semantic/entities/sale_event.py semantic/validators/identities.py tests/
git commit -m "feat(semantic): gross_amount 列 + 毛额守恒恒等式 — cancelled 纳入闭环, 审计 state 枚举完备性"
```

---

### Task 4: order_line 凭证账 CTE

第二本账:`sale_bill → sale_order → sale_order_product` 三表 JOIN,item 粒度独立算 qty/gross。时间语义用 `sb.finish_time`(凭证账完成时点;与统计账 `sp.complete_time` 的对齐度由 Task 12 观察跑实测)。

**Files:**
- Create: `semantic/entities/order_line.py`
- Create: `tests/test_order_line.py`

- [ ] **Step 1: 写失败测试**

```python
"""order_line 凭证账 CTE 的 SQL 渲染契约 (镜像 tests/test_sale_event.py 模式)。"""
import unittest

import tests._setup  # noqa: F401

from semantic.entities import order_line


def render(sql: str) -> str:
    return sql.format(project="p", dataset="d", start_ts=1, end_ts=2)


class TestOrderLineCte(unittest.TestCase):
    def setUp(self):
        self.sql = render(
            f"WITH {order_line.order_line_cte()} SELECT * FROM order_line")

    def test_three_table_join(self):
        self.assertIn("`p`.`d`.`ttpos_sale_order_product` sop", self.sql)
        self.assertIn("`p`.`d`.`ttpos_sale_order` so", self.sql)
        self.assertIn("`p`.`d`.`ttpos_sale_bill` sb", self.sql)

    def test_soft_delete_filters(self):
        # ttpos 软删约定: 三张表都要 delete_time = 0
        self.assertIn("so.delete_time = 0", self.sql)
        self.assertIn("sb.delete_time = 0", self.sql)
        self.assertIn("sop.delete_time = 0", self.sql)

    def test_only_completed_bills(self):
        self.assertIn("sb.status = 1", self.sql)

    def test_time_window_on_bill_finish_time(self):
        self.assertIn("sb.finish_time >= 1", self.sql)
        self.assertIn("sb.finish_time < 2", self.sql)

    def test_measures(self):
        self.assertIn("SUM(sop.num) AS voucher_qty", self.sql)
        self.assertIn("SUM(sop.sale_price * sop.num) AS voucher_gross", self.sql)
        self.assertIn("GROUP BY item_uuid", self.sql)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m unittest tests.test_order_line -v`
Expected: FAIL(module 不存在)

- [ ] **Step 3: 实现 order_line.py**

```python
"""Order line — 凭证账 (sale_bill → sale_order → sale_order_product)。

跟 sale_event (统计账, ttpos_statistics_product) 的关系:
  两者由 ttpos 后端**不同代码路径**写入, 是天然的两本账. CROSS_LEDGER
  恒等式 (semantic/validators/identities.py) 用本 CTE 对统计账做独立互证 —
  这是销量/金额恒等式从"循环永真"升级为"可证伪"的来源 (spec §3/§5 A1).

口径要点:
  - 只取已完成账单 (sb.status = 1), 时间窗在 sb.finish_time 上.
    与统计账 sp.complete_time 的对齐度由 2026-05 观察跑实测
    (scripts/adhoc/audit_cross_ledger_202605.py), 实测前 CROSS_LEDGER
    不进阻断名单.
  - 三表全部过 delete_time = 0 (ttpos 软删约定).
  - voucher_gross 用 sop.sale_price (折前标价) × num, 对齐统计账
    sales_price = product_sale_price × product_num 的折前口径.
"""


def order_line_cte() -> str:
    """Returns `order_line AS (...)`. 占位符同 sale_event — drop-in 兼容 engine.query()。"""
    return """order_line AS (
  SELECT
    sop.product_package_uuid AS item_uuid,
    SUM(sop.num) AS voucher_qty,
    SUM(sop.sale_price * sop.num) AS voucher_gross,
    SUM(sop.total_price) AS voucher_net,
    SUM(sop.discount_fee) AS voucher_discount
  FROM `{project}`.`{dataset}`.`ttpos_sale_order_product` sop
  JOIN `{project}`.`{dataset}`.`ttpos_sale_order` so
    ON so.uuid = sop.sale_order_uuid AND so.delete_time = 0
  JOIN `{project}`.`{dataset}`.`ttpos_sale_bill` sb
    ON sb.uuid = so.sale_bill_uuid AND sb.delete_time = 0
  WHERE sb.status = 1
    AND sb.finish_time >= {start_ts}
    AND sb.finish_time < {end_ts}
    AND sop.delete_time = 0
  GROUP BY item_uuid
)"""
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m unittest tests.test_order_line -v && venv/bin/python -m unittest discover tests 2>&1 | tail -3`
Expected: 全部 PASS / OK

- [ ] **Step 5: Commit**

```bash
git add semantic/entities/order_line.py tests/test_order_line.py
git commit -m "feat(semantic): order_line 凭证账 CTE — 统计账互证的独立来源"
```

---

### Task 5: 跨账本行构建器 + CROSS_LEDGER 恒等式

把统计账聚合行和凭证账聚合行按 (store_num, item_uuid) 合并成互证行;三条恒等式:qty 互证(零容差)、gross 互证(A 阶段沿用金额容忍带)、凭证覆盖(统计账有量但凭证账缺 → MUST_FIX,语义是"未经互证"而非"数字错")。**CROSS_LEDGER_IDENTITIES 是独立 bundle,不进 FULL_IDENTITIES / 不进闸门**——升级与否由 Task 12 观察跑的底数决定(spec §11 PR-A 验收线"只观察不阻断")。

**Files:**
- Create: `semantic/validators/cross_ledger.py`(行构建器——数据准备,不是恒等式,所以不进 identities.py)
- Modify: `semantic/validators/identities.py`(三条恒等式 + bundle)
- Create: `tests/test_cross_ledger.py`

- [ ] **Step 1: 写失败测试**

```python
"""跨账本互证: 行构建器 + CROSS_LEDGER 恒等式 (spec §5 A1)。"""
import unittest

import tests._setup  # noqa: F401

from semantic.validators import check
from semantic.validators.core import Severity
from semantic.validators.cross_ledger import build_cross_ledger_rows
from semantic.validators.identities import CROSS_LEDGER_IDENTITIES


def _stat(store="001", item="100", qty=10.0, gross=100.0):
    return {"store_num": store, "item_uuid": item, "item_name": "商品A",
            "qty": qty, "gross_amount": gross}


def _voucher(store="001", item="100", qty=10.0, gross=100.0):
    return {"store_num": store, "item_uuid": item,
            "voucher_qty": qty, "voucher_gross": gross}


class TestBuildCrossLedgerRows(unittest.TestCase):
    def test_matched_pair_merges(self):
        rows = build_cross_ledger_rows([_stat()], [_voucher()])
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["stat_qty"], 10.0)
        self.assertEqual(r["voucher_qty"], 10.0)
        self.assertEqual(r["voucher_present"], 1.0)

    def test_missing_voucher_flagged_not_zero_faked(self):
        rows = build_cross_ledger_rows([_stat()], [])
        self.assertEqual(rows[0]["voucher_present"], 0.0)

    def test_keyed_by_store_and_item(self):
        rows = build_cross_ledger_rows(
            [_stat(store="001"), _stat(store="002", qty=5.0)],
            [_voucher(store="001"), _voucher(store="002", qty=5.0)])
        self.assertEqual(len(rows), 2)
        by_store = {r["store_num"]: r for r in rows}
        self.assertEqual(by_store["002"]["voucher_qty"], 5.0)


class TestCrossLedgerIdentities(unittest.TestCase):
    def test_balanced_rows_pass(self):
        rows = build_cross_ledger_rows([_stat()], [_voucher()])
        result = check(rows, CROSS_LEDGER_IDENTITIES)
        self.assertEqual(result.violations, [])

    def test_qty_drift_is_must_fix(self):
        rows = build_cross_ledger_rows([_stat(qty=10.0)], [_voucher(qty=9.0)])
        result = check(rows, CROSS_LEDGER_IDENTITIES)
        self.assertTrue(any(v.severity == Severity.MUST_FIX
                            and v.identity.name == "跨账本销量互证"
                            for v in result.violations))

    def test_missing_voucher_is_must_fix_coverage(self):
        rows = build_cross_ledger_rows([_stat(qty=10.0)], [])
        result = check(rows, CROSS_LEDGER_IDENTITIES)
        names = {v.identity.name for v in result.violations
                 if v.severity == Severity.MUST_FIX}
        self.assertIn("凭证账覆盖完整性", names)

    def test_zero_qty_without_voucher_ok(self):
        # 统计账没量, 凭证账没行 — 不算缺
        rows = build_cross_ledger_rows([_stat(qty=0.0, gross=0.0)], [])
        result = check(rows, CROSS_LEDGER_IDENTITIES)
        self.assertEqual([v for v in result.violations
                          if v.identity.name == "凭证账覆盖完整性"], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m unittest tests.test_cross_ledger -v`
Expected: FAIL(module / bundle 不存在)

- [ ] **Step 3: 实现行构建器**

`semantic/validators/cross_ledger.py`:

```python
"""跨账本互证的行构建器 — 统计账聚合 × 凭证账聚合 → 互证行。

恒等式本体在 identities.py (CROSS_LEDGER_IDENTITIES, 口径只在那里收口);
本模块只做数据准备: 按 (store_num, item_uuid) 合并两边聚合结果.

凭证账缺行时 voucher_present=0.0 显式标记, 不伪造 0 值 — "未经互证"和
"数字为 0"是两回事 (spec §8 错误处理).
"""
from __future__ import annotations


def build_cross_ledger_rows(stat_rows: list[dict], voucher_rows: list[dict]) -> list[dict]:
    """stat_rows: 统计账聚合 (须有 store_num/item_uuid/qty/gross_amount).
    voucher_rows: 凭证账聚合 (须有 store_num/item_uuid/voucher_qty/voucher_gross).
    返回互证行, 一行一个 (store, item).
    """
    voucher_by_key = {(v["store_num"], v["item_uuid"]): v for v in voucher_rows}
    rows = []
    for s in stat_rows:
        v = voucher_by_key.get((s["store_num"], s["item_uuid"]))
        rows.append({
            "store_num": s["store_num"],
            "item_uuid": s["item_uuid"],
            "item_name": s.get("item_name", ""),
            "stat_qty": float(s.get("qty", 0) or 0),
            "stat_gross": float(s.get("gross_amount", 0) or 0),
            "voucher_qty": float(v["voucher_qty"]) if v else 0.0,
            "voucher_gross": float(v["voucher_gross"]) if v else 0.0,
            "voucher_present": 1.0 if v else 0.0,
        })
    return rows
```

- [ ] **Step 4: 实现三条恒等式**

`semantic/validators/identities.py` 在 Combined bundles 注释块之前加:

```python
# ═══════════════════════════════════════════════════════════════════
# A1 — Cross-Ledger Identities (跨账本互证: 统计账 vs 凭证账)
#
# row 由 semantic/validators/cross_ledger.build_cross_ledger_rows 构建.
# ⚠️ 独立 bundle, 不进 FULL_IDENTITIES / 不进导出闸门 — 时间语义对齐度
# (sp.complete_time vs sb.finish_time) 由 2026-05 观察跑实测后才决定升级
# (spec §11 PR-A 验收线; 决策记录见 docs/audit/2026-06-cross-ledger-baseline.md).
# ═══════════════════════════════════════════════════════════════════

CROSS_LEDGER_QTY = Identity(
    name="跨账本销量互证",
    description="统计账 qty == 凭证账 SUM(num) — 两本账独立写入, 互相可证伪",
    lhs=lambda r: r["stat_qty"],
    rhs=lambda r: r["voucher_qty"],
    classify=lambda d, lhs: (Severity.NEGLIGIBLE if d == 0 else Severity.MUST_FIX),
    fields=("stat_qty", "voucher_qty"),
)

CROSS_LEDGER_GROSS = Identity(
    name="跨账本毛额互证",
    description="统计账 gross_amount == 凭证账 SUM(sale_price×num) — PR-B 整数化后收零容差",
    lhs=lambda r: r["stat_gross"],
    rhs=lambda r: r["voucher_gross"],
    classify=_money_classify,
    fields=("stat_gross", "voucher_gross"),
)

VOUCHER_COVERAGE = Identity(
    name="凭证账覆盖完整性",
    description=(
        "统计账有销量 (stat_qty > 0) 的 (store, item) 必须在凭证账有行.\n"
        "违反语义是「该数字未经互证」, 不是「数字错了」— console 文案要区分."
    ),
    lhs=lambda r: 1.0 if (r["stat_qty"] > 0 and r["voucher_present"] == 0.0) else 0.0,
    rhs=lambda r: 0.0,
    classify=_coverage_classify,
    fields=("stat_qty", "voucher_present"),
)

CROSS_LEDGER_IDENTITIES = [CROSS_LEDGER_QTY, CROSS_LEDGER_GROSS, VOUCHER_COVERAGE]
```

(`Severity` 已由文件头 `from .core import Identity, Severity` 引入,无需新 import。)

- [ ] **Step 5: 跑测试确认通过 + 全量回归**

Run: `venv/bin/python -m unittest tests.test_cross_ledger -v && venv/bin/python -m unittest discover tests 2>&1 | tail -3`
Expected: 全部 PASS / OK

- [ ] **Step 6: Commit**

```bash
git add semantic/validators/cross_ledger.py semantic/validators/identities.py tests/test_cross_ledger.py
git commit -m "feat(validators): 跨账本互证 — 统计账×凭证账行构建器 + CROSS_LEDGER 恒等式 (暂不进闸门)"
```

---

### Task 6: takeout_tieout 订单粒度 CTE + 恒等式

外卖的"第二本账":item 级 `SUM(toi.price×quantity)` vs 订单级 `platform_total`(ttpos 主统计 CountTakeoutSale 的字段,pitfalls §5.1 点名的口径雷)。merchant_charge_fee / merchant_discount 是订单级字段,归宿在这里(不进 item 粒度的 sale_event——JOIN 后 SUM 会按订单内 item 数重复计数;此为对 spec §5 A2 的有意偏差,见计划头部)。华莱士当前两字段为 0,恒等式先按 `platform_total == item_sum` 校验;业务开启费用时它会 fire,那一刻再实测符号并修正描述。

**Files:**
- Create: `semantic/entities/takeout_tieout.py`
- Modify: `semantic/validators/identities.py`
- Create: `tests/test_takeout_tieout.py`

- [ ] **Step 1: 写失败测试**

```python
"""takeout_tieout: 外卖订单级 platform_total vs item 级求和 互证 CTE + 恒等式。"""
import unittest

import tests._setup  # noqa: F401

from semantic.entities import takeout_tieout
from semantic.validators import check
from semantic.validators.core import Severity
from semantic.validators.identities import TAKEOUT_TIEOUT_IDENTITIES


def render(sql: str) -> str:
    return sql.format(project="p", dataset="d", start_ts=1, end_ts=2)


class TestTakeoutTieoutCte(unittest.TestCase):
    def setUp(self):
        self.sql = render(
            f"WITH {takeout_tieout.takeout_tieout_cte()} SELECT * FROM takeout_tieout")

    def test_dynamic_time_condition(self):
        # pitfalls §1.3: state=40 用 completed_time, 其余用 accepted_time
        self.assertIn("t.order_state = 40 AND t.completed_time >= 1", self.sql)
        self.assertIn("t.order_state != 40 AND t.accepted_time >= 1", self.sql)

    def test_order_grain_measures(self):
        self.assertIn("t.platform_total AS platform_total", self.sql)
        self.assertIn("t.merchant_charge_fee AS merchant_charge_fee", self.sql)
        self.assertIn("t.merchant_discount AS merchant_discount", self.sql)
        self.assertIn("SUM(toi.price * toi.quantity) AS item_sum", self.sql)

    def test_soft_delete(self):
        self.assertIn("t.delete_time = 0", self.sql)


class TestTakeoutTieoutIdentities(unittest.TestCase):
    def _row(self, platform_total=100.0, item_sum=100.0, fee=0.0, disc=0.0):
        return {"order_uuid": "1", "platform_total": platform_total,
                "item_sum": item_sum, "merchant_charge_fee": fee,
                "merchant_discount": disc}

    def test_balanced_passes(self):
        result = check([self._row()], TAKEOUT_TIEOUT_IDENTITIES)
        self.assertEqual(result.violations, [])

    def test_drift_fires_capped_at_review(self):
        # 升级到 MUST_FIX 须等观察跑校准 (含 merchant 费用符号) — 先封顶 🟡
        result = check([self._row(item_sum=300.0)], TAKEOUT_TIEOUT_IDENTITIES)
        self.assertTrue(result.violations)
        self.assertTrue(all(v.severity == Severity.NEEDS_REVIEW
                            for v in result.violations))

    def test_nonzero_merchant_fee_fires(self):
        # 华莱士当前 fee=0; 业务开启费用即 fire, 提醒校准口径 (pitfalls §5.1)
        result = check([self._row(fee=5.0)], TAKEOUT_TIEOUT_IDENTITIES)
        self.assertTrue(result.violations)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m unittest tests.test_takeout_tieout -v`
Expected: FAIL(module 不存在)

- [ ] **Step 3: 实现 CTE**

`semantic/entities/takeout_tieout.py`:

```python
"""Takeout tieout — 外卖订单粒度互证行 (item 级求和 vs 订单级 platform_total)。

为什么独立于 sale_event:
  - platform_total / merchant_charge_fee / merchant_discount 是订单级字段,
    JOIN 到 item 行再 SUM 会按订单内 item 数重复计数 — 订单级量只能在订单粒度比
  - 这是外卖侧的"第二本账": RankTakeoutProduct (item 级, 我们抄的) vs
    CountTakeoutSale (订单级, ttpos 后台真口径), pitfalls §5.1
  - 华莱士当前 merchant 两字段恒为 0; 业务开启费用时 TAKEOUT_TIEOUT 恒等式
    会 fire, 届时实测符号关系再升级口径

时间条件沿用 pitfalls §1.3 动态规则 (state=40 用 completed_time).
"""


def takeout_tieout_cte() -> str:
    """Returns `takeout_tieout AS (...)`. 订单粒度, 一行一单。"""
    return """takeout_tieout AS (
  SELECT
    t.uuid AS order_uuid,
    t.order_state AS order_state,
    t.platform_total AS platform_total,
    t.merchant_charge_fee AS merchant_charge_fee,
    t.merchant_discount AS merchant_discount,
    SUM(toi.price * toi.quantity) AS item_sum
  FROM `{project}`.`{dataset}`.`ttpos_takeout_order` t
  JOIN `{project}`.`{dataset}`.`ttpos_takeout_order_item` toi
    ON toi.takeout_order_uuid = t.uuid AND toi.delete_time = 0
  WHERE t.delete_time = 0
    AND t.accepted_time > 0
    AND (
      (t.order_state = 40 AND t.completed_time >= {start_ts} AND t.completed_time < {end_ts})
      OR
      (t.order_state != 40 AND t.accepted_time >= {start_ts} AND t.accepted_time < {end_ts})
    )
  GROUP BY order_uuid, order_state, platform_total, merchant_charge_fee, merchant_discount
)"""
```

⚠️ 实施时核对 `toi` 表名/外键名:在 `semantic/entities/takeout_line.py` 里查现用的 item 表 JOIN 写法,保持一致(若实际是 `ttpos_takeout_order_item` 之外的名字,以 takeout_line.py 为准并同步改测试断言)。

- [ ] **Step 4: 实现恒等式(封顶 🟡 的 classify)**

`semantic/validators/identities.py` CROSS_LEDGER 块之后加:

```python
def _capped_review_money(delta: float, lhs: float) -> Severity:
    """金额分类但封顶 NEEDS_REVIEW — 给口径未校准的勾稽用 (spec §5 A1 支付勾稽同策)."""
    sev = _money_classify(delta, lhs)
    return Severity.NEEDS_REVIEW if sev == Severity.MUST_FIX else sev


TAKEOUT_TIEOUT_IDENTITY = Identity(
    name="外卖订单勾稽",
    description=(
        "platform_total == SUM(toi.price×quantity) + merchant_charge_fee"
        " + merchant_discount (符号待观察跑校准, 华莱士当前 merchant 字段恒 0).\n"
        "封顶 🟡: 升 MUST_FIX 须等口径校准 (docs/audit/2026-06-cross-ledger-baseline.md)."
    ),
    lhs=lambda r: r["platform_total"],
    rhs=lambda r: (r["item_sum"] - r["merchant_charge_fee"]
                   - r["merchant_discount"]),
    classify=_capped_review_money,
    fields=("platform_total", "item_sum", "merchant_charge_fee", "merchant_discount"),
)

TAKEOUT_TIEOUT_IDENTITIES = [TAKEOUT_TIEOUT_IDENTITY]


PAYMENT_TIEOUT_IDENTITY = Identity(
    name="支付勾稽",
    description=(
        "店×月粒度: SUM(sale_bill.payment_amount) vs 统计账实收 (actual_amount).\n"
        "封顶 🟡 (spec §5 A1): service_fee/tax_fee/整单折扣的口径映射待"
        " 2026-05 观察跑校准, 校准前不转红线 (CLAUDE.md 技术债 ②).\n"
        "row 字段: payment_amount_sum / stat_actual_sum (由观察跑/报表层构造)."
    ),
    lhs=lambda r: r["payment_amount_sum"],
    rhs=lambda r: r["stat_actual_sum"],
    classify=_capped_review_money,
    fields=("payment_amount_sum", "stat_actual_sum"),
)
```

(支付勾稽的店级行由 Task 12 观察跑构造并实测;报表层接入与否按基线报告结论定。)

- [ ] **Step 5: 跑测试确认通过 + 全量回归 + Commit**

Run: `venv/bin/python -m unittest tests.test_takeout_tieout -v && venv/bin/python -m unittest discover tests 2>&1 | tail -3`
Expected: 全部 PASS / OK

```bash
git add semantic/entities/takeout_tieout.py semantic/validators/identities.py tests/test_takeout_tieout.py
git commit -m "feat(semantic): 外卖订单勾稽 — item级求和 vs platform_total, merchant 字段显式纳管 (封顶🟡)"
```

---

### Task 7: 导出闸门 gate.py(硬阻断 + --force 水印)

全部脚本的统一收口。语义:`has_must_fix` 且无 `--force` → 打印后 `sys.exit(2)`,不产出文件;`--force` → 返回 outcome,脚本写盘后调水印 helper(xlsxwriter 用 `activate()+set_first_sheet()` 让水印页成为打开时第一眼;openpyxl 直接插 index 0)。空导出(0 行)按 must_fix 处理——"成功导出一张空表"是无声错误。

**Files:**
- Create: `semantic/validators/gate.py`
- Modify: `semantic/validators/__init__.py`(导出 gate 符号)
- Create: `tests/test_gate.py`

- [ ] **Step 1: 写失败测试**

```python
"""导出闸门: 阻断 / 放行 / --force 水印 三态行为 (spec §5 A4)。"""
import io
import unittest
from contextlib import redirect_stdout

import tests._setup  # noqa: F401

from semantic.validators.gate import validate_and_gate, GateOutcome
from semantic.validators.identities import DEFAULT_IDENTITIES


def _good_row():
    return {
        "qty": 100.0, "net_qty": 80.0, "free_qty": 5.0, "give_qty": 5.0,
        "refund_qty": 6.0, "cancelled_qty": 4.0,
        "sales_price": 1000.0, "revenue": 800.0, "refund_amount": 60.0,
        "free_amount": 50.0, "give_amount": 50.0, "discount_amount": 40.0,
        "cancelled_amount": 30.0, "gross_amount": 1030.0,
    }


def _bad_row():
    row = _good_row()
    row["revenue"] += 500.0  # 穿透容忍带 → MUST_FIX
    return row


class TestGate(unittest.TestCase):
    def test_clean_rows_pass_through(self):
        out = validate_and_gate([_good_row()], DEFAULT_IDENTITIES,
                                force=False, report_name="t")
        self.assertIsInstance(out, GateOutcome)
        self.assertFalse(out.needs_watermark)

    def test_must_fix_blocks_with_exit_2(self):
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
            validate_and_gate([_bad_row()], DEFAULT_IDENTITIES,
                              force=False, report_name="t")
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("零容差", buf.getvalue())

    def test_force_returns_with_watermark(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            out = validate_and_gate([_bad_row()], DEFAULT_IDENTITIES,
                                    force=True, report_name="t")
        self.assertTrue(out.needs_watermark)
        lines = out.watermark_lines()
        self.assertTrue(any("未通过" in ln for ln in lines))

    def test_empty_export_blocks(self):
        """0 行也是无声错误 — '成功导出空表' 不许发生."""
        with self.assertRaises(SystemExit) as cm:
            validate_and_gate([], DEFAULT_IDENTITIES, force=False, report_name="t")
        self.assertEqual(cm.exception.code, 2)


class TestWatermarkHelpers(unittest.TestCase):
    def test_openpyxl_watermark_first_sheet(self):
        import openpyxl
        from semantic.validators.gate import add_watermark_sheet_openpyxl
        wb = openpyxl.Workbook()
        wb.active.title = "数据"
        add_watermark_sheet_openpyxl(wb, ["⚠️ 本表未通过零容差校验", "强制导出"])
        self.assertEqual(wb.sheetnames[0], "⚠️校验未通过")

    def test_xlsxwriter_watermark_activated(self):
        import os
        import tempfile
        import xlsxwriter
        from semantic.validators.gate import add_watermark_sheet_xlsxwriter
        path = os.path.join(tempfile.mkdtemp(), "t.xlsx")
        wb = xlsxwriter.Workbook(path)
        wb.add_worksheet("数据")
        ws = add_watermark_sheet_xlsxwriter(wb, ["⚠️ 本表未通过零容差校验"])
        self.assertEqual(ws.name, "⚠️校验未通过")
        wb.close()
        self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m unittest tests.test_gate -v`
Expected: FAIL(module 不存在)

- [ ] **Step 3: 实现 gate.py**

```python
"""导出闸门 — 零容差校验失败时硬阻断, --force 留逃生口但水印可见 (spec §2 决策 5)。

任何把数据写到 Excel/CSV 的报表脚本, 写盘前必须调 validate_and_gate():

    from semantic.validators.gate import validate_and_gate
    outcome = validate_and_gate(check_rows, FULL_IDENTITIES,
                                force=args.force, report_name="profit_margin",
                                row_label=lambda r: f"店 {r['store_num']}")
    # ... 写盘 ...
    if outcome.needs_watermark:
        add_watermark_sheet_xlsxwriter(wb, outcome.watermark_lines())  # 或 openpyxl 版

行为:
  - 全绿 / 仅 🟡         → 返回 GateOutcome, 正常写盘
  - 有 🔴 且无 force     → 打印违反清单, sys.exit(2), 不产出文件
  - 有 🔴 且 force=True  → 返回 needs_watermark=True, 脚本写盘后必须打水印
  - check_rows 为空      → 按 🔴 处理 ("成功导出空表"是无声错误)

结构性测试 (tests/test_validator_coverage.py) 强制 19 个报表脚本全部接入.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable

from .core import Identity, Result, Severity, check, print_result

EXIT_GATE_BLOCKED = 2


@dataclass
class GateOutcome:
    result: Result
    forced: bool
    report_name: str
    empty_blocked: bool = False  # 空导出被 --force 放行 — 也要水印

    @property
    def needs_watermark(self) -> bool:
        return self.forced and (self.result.has_must_fix or self.empty_blocked)

    def watermark_lines(self) -> list[str]:
        must = [v for v in self.result.violations
                if v.severity == Severity.MUST_FIX]
        detail = ("数据为空 (0 行)" if self.empty_blocked
                  else f"🔴 离谱违反: {len(must)} 条")
        return [
            "⚠️ 本表未通过零容差校验 (--force 强制导出)",
            f"报表: {self.report_name}   {detail}",
            "本文件中的数字未经数学校验背书, 不得作为对外交付口径.",
            "违反明细见导出时的 console 输出.",
        ]


def validate_and_gate(
    check_rows: list[dict],
    identities: list[Identity],
    *,
    force: bool,
    report_name: str,
    row_label: Callable[[dict], str] = lambda r: str(r),
    min_rows: int = 1,
) -> GateOutcome:
    if len(check_rows) < min_rows:
        print(f"🔴 [{report_name}] 零容差闸门: check_rows 仅 {len(check_rows)} 行 "
              f"(< {min_rows}) — 空导出按无声错误阻断.")
        if not force:
            sys.exit(EXIT_GATE_BLOCKED)
        return GateOutcome(result=check([], identities), forced=True,
                           report_name=report_name, empty_blocked=True)

    result = check(check_rows, identities)
    print(f"\n[{report_name}] 零容差闸门校验 ({len(check_rows)} 行 × {len(identities)} 条恒等式)")
    print_result(result, row_label=row_label)

    if result.has_must_fix and not force:
        print(f"🔴 [{report_name}] 零容差闸门: 有离谱违反, 已阻断导出 (exit {EXIT_GATE_BLOCKED}).")
        print("    修复数据/口径后重跑; 或 --force 强制导出 (文件将带水印, 不得对外交付).")
        sys.exit(EXIT_GATE_BLOCKED)
    if result.has_must_fix:
        print(f"⚠️  [{report_name}] --force 强制导出: 文件将打水印.")
    return GateOutcome(result=result, forced=force, report_name=report_name)


WATERMARK_SHEET_NAME = "⚠️校验未通过"


def add_watermark_sheet_xlsxwriter(workbook, lines: list[str]):
    """xlsxwriter 无法事后调 sheet 顺序, 用 activate+set_first_sheet 让水印页
    成为打开文件的第一眼."""
    ws = workbook.add_worksheet(WATERMARK_SHEET_NAME)
    fmt = workbook.add_format(
        {"bold": True, "font_color": "white", "bg_color": "#C00000", "font_size": 14})
    for i, line in enumerate(lines):
        ws.write(i, 0, line, fmt)
    ws.set_column(0, 0, 90)
    ws.activate()
    ws.set_first_sheet()
    return ws


def add_watermark_sheet_openpyxl(workbook, lines: list[str]):
    from openpyxl.styles import Font, PatternFill
    ws = workbook.create_sheet(WATERMARK_SHEET_NAME, 0)
    fill = PatternFill("solid", fgColor="C00000")
    font = Font(bold=True, color="FFFFFF", size=14)
    for i, line in enumerate(lines, start=1):
        cell = ws.cell(row=i, column=1, value=line)
        cell.fill = fill
        cell.font = font
    ws.column_dimensions["A"].width = 90
    workbook.active = 0
    return ws
```

`semantic/validators/__init__.py` 追加导出:

```python
from .gate import (
    GateOutcome,
    add_watermark_sheet_openpyxl,
    add_watermark_sheet_xlsxwriter,
    validate_and_gate,
)
```

并把这四个名字加进 `__all__`。

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `venv/bin/python -m unittest tests.test_gate -v && venv/bin/python -m unittest discover tests 2>&1 | tail -3`
Expected: 全部 PASS / OK

- [ ] **Step 5: Commit**

```bash
git add semantic/validators/gate.py semantic/validators/__init__.py tests/test_gate.py
git commit -m "feat(validators): 导出闸门 — must_fix 硬阻断 exit 2, --force 水印逃生口, 空导出视为无声错误"
```

---

### Task 8: AST 覆盖测试(收缩名单驱动接入)

结构性检查:`bq_reports/*.py` 每个脚本必须 import `semantic.validators.gate` 且引用 `validate_and_gate`。用 `PENDING` 名单豁免未接脚本——**每接一个删一行,名单只许收缩**;测试同时断言名单里的脚本确实未接(防名单过期)。Task 9-11 的验收就是把名单清空。

**Files:**
- Create: `tests/test_validator_coverage.py`

- [ ] **Step 1: 写测试(初始 PENDING=16,本 task 即绿)**

```python
"""结构性覆盖: 报表脚本必须接导出闸门 (CLAUDE.md 第 3 节"无例外"的机制化)。

PENDING 是未接脚本的收缩名单 — 只许删不许加. 新脚本不接闸门, 本测试直接挂.
"""
import ast
import unittest
from pathlib import Path

import tests._setup  # noqa: F401
from tests._setup import REPO_ROOT

# 收缩名单: Task 9-11 每接一个脚本删一行. 全空后删除本常量与豁免逻辑.
PENDING = {
    # Task 9 (engine 系)
    "pnl_statement.py",
    "profit_margin_report.py",
    "profit_by_price_report.py",
    "report_sales_period_bq.py",
    # Task 10 (bq_exporter 系)
    "report_bom_sales_bq.py",
    "report_daily_sales_bq.py",
    "report_material_stats_bq.py",
    "report_orders_by_nationality_bq.py",
    "report_sales_consumption_bq.py",
    # Task 11 (standalone 系)
    "bom_export_report.py",
    "deleted_bom_report.py",
    "deleted_combo_bom_report.py",
    "deleted_single_bom_report.py",
    "export_all_menu_bilingual.py",
    "menu_no_bom_bilingual.py",
    "menu_no_bom_from_sales.py",
    "report_daily_item_sales_bq.py",
    "report_item_sales_weekly_bq.py",
    "report_sales_simple_bq.py",
}


def _uses_gate(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and \
                "semantic.validators" in node.module:
            if any(a.name in ("validate_and_gate", "gate") for a in node.names):
                return True
        if isinstance(node, ast.Name) and node.id == "validate_and_gate":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "validate_and_gate":
            return True
    return False


class TestValidatorCoverage(unittest.TestCase):
    def _report_scripts(self):
        return sorted(p for p in (REPO_ROOT / "bq_reports").glob("*.py")
                      if p.name != "__init__.py")

    def test_inventory_matches(self):
        """脚本清单变动 (新增/删除) 必须显式过本测试 — 防 PENDING 名单失效."""
        names = {p.name for p in self._report_scripts()}
        unknown = PENDING - names
        self.assertFalse(unknown, f"PENDING 里有不存在的脚本: {unknown}")

    def test_wired_scripts_use_gate(self):
        for p in self._report_scripts():
            if p.name in PENDING:
                continue
            self.assertTrue(_uses_gate(p),
                            f"{p.name} 必须接导出闸门 (validate_and_gate) — "
                            f"CLAUDE.md 第 3 节, 无例外")

    def test_pending_scripts_actually_pending(self):
        """防名单过期: 已接的脚本必须从 PENDING 删掉."""
        for p in self._report_scripts():
            if p.name in PENDING:
                self.assertFalse(_uses_gate(p),
                                 f"{p.name} 已接闸门, 从 PENDING 删除它")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认绿(当前 19 个全在 PENDING,但 3 个旧接法脚本只 import 了 check 不算 gate)**

Run: `venv/bin/python -m unittest tests.test_validator_coverage -v`
Expected: 3 个测试全 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_validator_coverage.py
git commit -m "test: AST 覆盖测试 — 报表脚本接闸门的结构性强制, PENDING 收缩名单驱动"
```

---

### Task 9: 接入 engine 系 4 脚本(profit_margin / profit_by_price / report_sales_period / pnl_statement)

前 3 个已有"建议性 check"(只打印不阻断),换成闸门;pnl_statement 从零接。这 4 个都有销售指标,用 `FULL_IDENTITIES`。同时把 Task 3 的 `gross_amount` 接进各自聚合(METRIC keys + check_rows)。

**Files:**
- Modify: `bq_reports/profit_margin_report.py:1604-1622`(check→gate)及其 argparse、聚合 METRIC keys
- Modify: `bq_reports/profit_by_price_report.py:52-53,745-749` 同上
- Modify: `bq_reports/report_sales_period_bq.py:43-44,678-700` 同上(xlsxwriter 裸写,水印用 xlsxwriter 版)
- Modify: `bq_reports/pnl_statement.py`(新接;check_rows 从 §1024 一带的 menu_rows 聚合构建)
- Modify: `tests/test_validator_coverage.py`(PENDING 删 4 行)

每个脚本的接入模式(以 profit_margin 为例,其余同模式适配):

- [ ] **Step 1: argparse 加 --force**

在脚本的 `argparse.ArgumentParser` 参数定义区加:

```python
    parser.add_argument("--force", action="store_true",
                        help="校验失败仍强制导出 (文件带水印, 不得对外交付)")
```

- [ ] **Step 2: 聚合接 gross_amount**

`profit_margin_report.py` 聚合初始化(约 :999 的 `data[key] = {...}` 初始字典)与累加处(约 :1023 一带)各加 `gross_amount` 键,跟 `revenue` 同模式从查询行读 `getattr(row, "gross_amount", 0)`。`_build_rows`/`_build_summary_rows` 构建 check_rows 的 `**data` 展开会自动带上,无需改 check_rows 构造。

- [ ] **Step 3: check→gate 替换**

`profit_margin_report.py:1604-1622` 的现有块:

```python
        from semantic.validators import check, print_result
        from semantic.validators.identities import FULL_IDENTITIES
        ...
        result = check(check_rows, FULL_IDENTITIES)
        print_result(result, row_label=...)
        if result.has_must_fix:
            print(f"⚠️  [{item_label}] 有 🔴 离谱违反，请核实数据/口径。\n")
```

替换为:

```python
        from semantic.validators.gate import (
            add_watermark_sheet_xlsxwriter, validate_and_gate)
        from semantic.validators.identities import FULL_IDENTITIES

        check_rows = [
            {"store_num": store_num, "item_name": item_name, **data}
            for (store_num, _store_name, _uuid, item_name), data
            in agg_data.items()
        ]
        outcome = validate_and_gate(
            check_rows, FULL_IDENTITIES,
            force=args.force, report_name=f"profit_margin/{item_label}",
            row_label=lambda r: f"店 {r['store_num']:>3}  {r['item_name']:<30}")
        if outcome.needs_watermark:
            add_watermark_sheet_xlsxwriter(wb, outcome.watermark_lines())
```

⚠️ 关键点:**gate 必须在 `wb.close()` 之前调用**——xlsxwriter 在 close 时才落盘,gate 在 close 前 exit 2 则磁盘上没有可用文件。profit_margin 现有 check 块本来就在 `wb.close()` 之前(:1597 write_sheet 之后、:1623 wb.close 之前),位置不变。多 sheet 循环里多次调 gate 时,水印 helper 会因重名 sheet 报错——用模块级 flag 只打一次水印(第一次 needs_watermark 时)。

- [ ] **Step 4: 其余 3 个脚本同模式接入**

- `profit_by_price_report.py`:check 调用在 :745-749,`engine.write_sheet` 在 :728,同为 xlsxwriter。check_rows 构造已存在(:737 一带,含 `net_qty`/`revenue` 推导),保留构造、替换 check→gate。
- `report_sales_period_bq.py`:check 在 :699-700,check_rows 构造 :678-692(注释自带"用 qty - others 当 net_qty"),xlsxwriter 裸写——找到 `workbook.close()` 调用点,gate 放它之前。
- `pnl_statement.py`:从零接。check_rows 用 :1024 一带已有的 `{"revenue": e["actual"], ...}` menu_rows 源头的聚合 dict;它是 P&L 报表,行结构与销售恒等式字段不完全对齐——**只用对齐的子集**:对 menu_rows 跑 `[GROSS_AMOUNT_IDENTITY, AMOUNT_IDENTITY]` 需要的字段若不齐,先用 Task 11 Step 1 的 `make_required_fields_identity`(必填字段非空)+ `min_rows` 起步,并在脚本注释里写"销售恒等式接入待 P&L 行字段对齐,技术债"。宁可基线校验也不许裸奔,但不许硬凑字段假装对账。

- [ ] **Step 5: PENDING 删 4 行,跑覆盖测试 + 全量回归**

Run: `venv/bin/python -m unittest tests.test_validator_coverage tests.test_gate -v && venv/bin/python -m unittest discover tests 2>&1 | tail -3`
Expected: 全部 PASS(`test_pending_scripts_actually_pending` 若 FAIL 说明漏删名单)

- [ ] **Step 6: 冒烟验证(真数据,2026-06 当月)**

Run: `venv/bin/python -m bq_reports.profit_margin_report --month 2026-06 --output exports/smoke_gate_test.xlsx 2>&1 | tail -15`
Expected: console 出现"零容差闸门校验"字样;exit code 0(数据干净)或 2(有违反——此时确认无文件产出,即闸门生效)。把观察到的行为记进 commit message。

- [ ] **Step 7: Commit**

```bash
git add bq_reports/profit_margin_report.py bq_reports/profit_by_price_report.py \
        bq_reports/report_sales_period_bq.py bq_reports/pnl_statement.py \
        tests/test_validator_coverage.py
git commit -m "feat(reports): engine 系 4 脚本接导出闸门 — 建议性 check 升级为硬阻断"
```

---

### Task 10: bq_exporter 闸门集成 + 5 个脚本

`bq_reports/utils/bq_exporter.py` 是 5 个脚本的统一写盘点(BaseExporter.write_excel `wb.save`:221 openpyxl;MultiShopExporter `wb.close`:264 xlsxwriter)。在 exporter 层加 gate 钩子,脚本只传配置。注意 bq_exporter 自带旧 `DataValidator`/`ValidationChain`(:112/:140)——**不动它,但新闸门是唯一阻断者**;旧链只是数据清洗辅助。

**Files:**
- Modify: `bq_reports/utils/bq_exporter.py`
- Modify: 5 个脚本(`report_bom_sales_bq.py`、`report_daily_sales_bq.py`、`report_material_stats_bq.py`、`report_orders_by_nationality_bq.py`、`report_sales_consumption_bq.py`)
- Create: `tests/test_bq_exporter_gate.py`
- Modify: `tests/test_validator_coverage.py`(PENDING 删 5 行)

- [ ] **Step 1: 写失败测试**

```python
"""bq_exporter 的闸门钩子: set_gate 后 write_excel 前必过闸。"""
import unittest

import tests._setup  # noqa: F401

from bq_reports.utils.bq_exporter import BaseExporter
from semantic.validators.gate import GateSpec
from semantic.validators.identities import DEFAULT_IDENTITIES


class TestExporterGate(unittest.TestCase):
    def test_gate_spec_holds_config(self):
        spec = GateSpec(identities=DEFAULT_IDENTITIES, force=False,
                        report_name="daily_sales",
                        build_check_rows=lambda rows: rows)
        self.assertEqual(spec.report_name, "daily_sales")

    def test_exporter_accepts_gate(self):
        exporter = BaseExporter.__new__(BaseExporter)  # 不触发 BQ 连接
        exporter.gate_spec = None
        spec = GateSpec(identities=DEFAULT_IDENTITIES, force=False,
                        report_name="t", build_check_rows=lambda rows: rows)
        exporter.set_gate(spec)
        self.assertIs(exporter.gate_spec, spec)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m unittest tests.test_bq_exporter_gate -v`
Expected: FAIL(GateSpec 不存在)

- [ ] **Step 3: gate.py 加 GateSpec,bq_exporter 加钩子**

`semantic/validators/gate.py` 追加:

```python
@dataclass
class GateSpec:
    """打包闸门配置, 给集中式写盘层 (bq_exporter) 用 — 脚本声明, exporter 执行.

    build_check_rows: 把 exporter 查到的原始行转成恒等式可读的 check_rows.
    """
    identities: list
    force: bool
    report_name: str
    build_check_rows: Callable[[list], list]
    row_label: Callable[[dict], str] = staticmethod(lambda r: str(r))

    def run(self, raw_rows: list) -> GateOutcome:
        return validate_and_gate(
            self.build_check_rows(raw_rows), self.identities,
            force=self.force, report_name=self.report_name,
            row_label=self.row_label)
```

`bq_reports/utils/bq_exporter.py` `BaseExporter` 加:

```python
    def set_gate(self, gate_spec):
        """零容差导出闸门 (semantic.validators.gate.GateSpec).
        设置后 write_excel 写盘前强制过闸; 未设置 = 该脚本必须以其他方式
        直调 validate_and_gate (tests/test_validator_coverage.py 强制)."""
        self.gate_spec = gate_spec
```

`BaseExporter.write_excel`(wb.save:221 之前)与 `MultiShopExporter.export`(wb.close:264 之前)各加:

```python
        outcome = None
        if getattr(self, "gate_spec", None) is not None:
            outcome = self.gate_spec.run(rows)   # 阻断时在此 exit 2, 不落盘
```

落盘前(openpyxl 在 wb.save 前 / xlsxwriter 在 wb.close 前)加:

```python
        if outcome is not None and outcome.needs_watermark:
            from semantic.validators.gate import add_watermark_sheet_openpyxl
            add_watermark_sheet_openpyxl(wb, outcome.watermark_lines())
```

(MultiShopExporter 用 `add_watermark_sheet_xlsxwriter`。`rows` 取该方法作用域里即将写盘的数据行变量名,实施时按实际变量对齐。)

- [ ] **Step 4: 5 个脚本接 GateSpec**

每个脚本(以 `report_daily_sales_bq.py` 为例,main() 在 :18,exporter 构造在 :30):argparse 加 `--force`;构造 exporter 后、调 export_* 之前加:

```python
    from semantic.validators.gate import GateSpec, validate_and_gate  # noqa: F401  (validate_and_gate 供覆盖测试识别)
    from semantic.validators.identities import SALES_QTY_IDENTITY, GROSS_AMOUNT_IDENTITY

    exporter.set_gate(GateSpec(
        identities=[SALES_QTY_IDENTITY, GROSS_AMOUNT_IDENTITY],
        force=args.force,
        report_name="daily_sales",
        build_check_rows=lambda rows: [dict(r) if isinstance(r, dict) else r._asdict() if hasattr(r, "_asdict") else vars(r) for r in rows],
    ))
```

**identities 按脚本实际字段选**(实施时打开每个脚本看 SQL SELECT 列,对照下表;字段不齐就退到 Task 11 Step 1 的 `make_required_fields_identity` 基线,并在脚本里注释"销售恒等式待字段补齐,技术债"):

| 脚本 | 数据形态 | 起步 identities |
|---|---|---|
| report_daily_sales_bq | 单品日销量 | qty 类(若有桶字段)否则必填基线 |
| report_bom_sales_bq | BOM×销量 | 必填基线(item_name、qty>0) |
| report_material_stats_bq | 物料统计 | 必填基线(material 名、用量) |
| report_orders_by_nationality_bq | 国籍×订单 | 必填基线(订单数>0) |
| report_sales_consumption_bq | 销售×消耗 | qty 类或必填基线 |

- [ ] **Step 5: PENDING 删 5 行,跑测试 + 回归 + Commit**

Run: `venv/bin/python -m unittest tests.test_bq_exporter_gate tests.test_validator_coverage -v && venv/bin/python -m unittest discover tests 2>&1 | tail -3`
Expected: 全部 PASS / OK

```bash
git add bq_reports/utils/bq_exporter.py bq_reports/report_*_bq.py \
        semantic/validators/gate.py tests/
git commit -m "feat(reports): bq_exporter 系 5 脚本接闸门 — GateSpec 集中式钩子"
```

---

### Task 11: standalone 系 10 脚本(BOM 4 + 菜单 3 + 销售 3)

非销售类导出(BOM/菜单)用不了销量恒等式,先立"必填字段基线"恒等式工厂:每行关键列非空。配合 gate 的 `min_rows=1`,非销售导出的"可靠"= 非空 + 必填列齐 + 无重复主键。

**Files:**
- Modify: `semantic/validators/identities.py`(工厂函数)
- Create: `tests/test_required_fields_identity.py`
- Modify: 10 个脚本(清单见 Step 4 表)
- Modify: `tests/test_validator_coverage.py`(PENDING 清空 + 删豁免逻辑)

- [ ] **Step 1: 写失败测试(工厂)**

```python
"""make_required_fields_identity / make_unique_key_identity — 非销售导出的基线恒等式。"""
import unittest

import tests._setup  # noqa: F401

from semantic.validators import check
from semantic.validators.core import Severity
from semantic.validators.identities import (
    make_required_fields_identity,
    make_unique_key_identity,
)


class TestRequiredFields(unittest.TestCase):
    def setUp(self):
        self.ident = make_required_fields_identity(
            ("item_name", "material_name"), name="BOM导出必填")

    def test_complete_row_passes(self):
        result = check([{"item_name": "汉堡", "material_name": "面包"}], [self.ident])
        self.assertEqual(result.violations, [])

    def test_empty_field_must_fix(self):
        result = check([{"item_name": "汉堡", "material_name": ""}], [self.ident])
        self.assertTrue(any(v.severity == Severity.MUST_FIX
                            for v in result.violations))

    def test_missing_key_must_fix(self):
        result = check([{"item_name": "汉堡"}], [self.ident])
        self.assertTrue(result.violations)


class TestUniqueKey(unittest.TestCase):
    def test_duplicate_key_fires(self):
        ident, prepare = make_unique_key_identity(("store", "item"), name="主键唯一")
        rows = prepare([{"store": "1", "item": "A"}, {"store": "1", "item": "A"}])
        result = check(rows, [ident])
        self.assertTrue(any(v.severity == Severity.MUST_FIX
                            for v in result.violations))

    def test_unique_rows_pass(self):
        ident, prepare = make_unique_key_identity(("store", "item"), name="主键唯一")
        rows = prepare([{"store": "1", "item": "A"}, {"store": "1", "item": "B"}])
        result = check(rows, [ident])
        self.assertEqual(result.violations, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败,实现工厂**

Run: `venv/bin/python -m unittest tests.test_required_fields_identity -v` → FAIL

`semantic/validators/identities.py` 加:

```python
# ═══════════════════════════════════════════════════════════════════
# 非销售导出基线 (BOM / 菜单 / 名录类报表)
#
# 这些报表没有销量/金额桶, "可靠"的最低数学含义是: 行非空 (gate min_rows)、
# 必填列非空、主键不重复. 比裸奔强一个数量级, 但明确不是对账 — 描述里写清.
# ═══════════════════════════════════════════════════════════════════

def make_required_fields_identity(required: tuple, name: str) -> Identity:
    """必填字段非空基线. row 缺 key 或值为空串/None → MUST_FIX."""
    def _missing_count(r: dict) -> float:
        return float(sum(
            1 for f in required
            if f not in r or r[f] is None or (isinstance(r[f], str) and not r[f].strip())
        ))
    return Identity(
        name=name,
        description=f"必填字段非空: {', '.join(required)} (基线校验, 非对账)",
        lhs=_missing_count,
        rhs=lambda r: 0.0,
        classify=_coverage_classify,
        fields=tuple(required),
    )


def make_unique_key_identity(key_fields: tuple, name: str):
    """主键唯一基线. 返回 (identity, prepare) — prepare 给每行标注 _dup_count,
    identity 检查它为 0. (core.check 是 row-local 的, 跨行去重只能预处理.)"""
    def prepare(rows: list) -> list:
        seen: dict = {}
        for r in rows:
            k = tuple(r.get(f) for f in key_fields)
            seen[k] = seen.get(k, 0) + 1
        return [{**r, "_dup_count": float(seen[tuple(r.get(f) for f in key_fields)] - 1)}
                for r in rows]
    ident = Identity(
        name=name,
        description=f"主键唯一: ({', '.join(key_fields)}) 重复 = 数据放大, MUST_FIX",
        lhs=lambda r: r["_dup_count"],
        rhs=lambda r: 0.0,
        classify=_coverage_classify,
        fields=("_dup_count",),
    )
    return ident, prepare
```

Run: `venv/bin/python -m unittest tests.test_required_fields_identity -v` → PASS

- [ ] **Step 3: Commit 工厂**

```bash
git add semantic/validators/identities.py tests/test_required_fields_identity.py
git commit -m "feat(validators): 必填字段/主键唯一基线恒等式工厂 — 非销售导出的最低数学保证"
```

- [ ] **Step 4: 逐脚本接入(三批,每批一个 commit)**

通用模式(每个脚本):argparse 加 `--force`(`report_daily_item_sales_bq.py`、`report_sales_simple_bq.py` 无 argparse,加最小 parser 只含 `--force` 与现有 sys.argv 用法兼容);在 `wb.save(...)`(openpyxl)或 `workbook.close()`(xlsxwriter)**之前**插入:

```python
    from semantic.validators.gate import (
        add_watermark_sheet_openpyxl, validate_and_gate)
    from semantic.validators.identities import (
        make_required_fields_identity, make_unique_key_identity)

    uniq_ident, prepare = make_unique_key_identity(("item_name",), name="商品名唯一")
    check_rows = prepare([
        {"item_name": name, "material_name": mat}   # ← 按脚本实际行变量构造
        for name, mat in export_rows
    ])
    outcome = validate_and_gate(
        check_rows,
        [make_required_fields_identity(("item_name",), name="菜单导出必填"), uniq_ident],
        force=args.force, report_name="menu_no_bom_bilingual",
        row_label=lambda r: r["item_name"])
    if outcome.needs_watermark:
        add_watermark_sheet_openpyxl(wb, outcome.watermark_lines())
    wb.save(output_path)
```

各脚本接入点与起步 identities(写盘点行号来自普查,实施时以实际为准):

| 脚本 | 写盘点 | 库 | 起步 identities(实施时按实际列名调整) |
|---|---|---|---|
| bom_export_report.py | wb.save:376 | openpyxl | 必填(商品名、物料名、用量)+ 主键唯一(商品,物料) |
| deleted_bom_report.py | wb.save:303 | openpyxl | 同上 |
| deleted_combo_bom_report.py | wb.save:183 | openpyxl | 同上 |
| deleted_single_bom_report.py | wb.save:300 | openpyxl | 同上 |
| export_all_menu_bilingual.py | wb.save:93 | openpyxl | 必填(中文名、英文名)+ 主键唯一(商品 uuid) |
| menu_no_bom_bilingual.py | wb.save:219 | openpyxl | 同上 |
| menu_no_bom_from_sales.py | wb.save:115 | openpyxl | 同上 |
| report_daily_item_sales_bq.py | ws.write 后的 save/close | openpyxl | qty 桶字段若齐用 SALES_QTY+GROSS,否则必填基线 |
| report_item_sales_weekly_bq.py | workbook.close (xlsxwriter) | xlsxwriter | 同上(水印用 xlsxwriter 版) |
| report_sales_simple_bq.py | save/close | openpyxl | 同上 |

每批完成后:PENDING 删对应行,跑 `venv/bin/python -m unittest tests.test_validator_coverage -v` 确认绿。

三批 commits:

```bash
git commit -m "feat(reports): BOM 系 4 脚本接闸门 (必填+主键唯一基线)"
git commit -m "feat(reports): 菜单系 3 脚本接闸门 (双语必填+uuid 唯一)"
git commit -m "feat(reports): standalone 销售系 3 脚本接闸门 — PENDING 清空"
```

- [ ] **Step 5: 清空 PENDING 后删豁免逻辑**

`tests/test_validator_coverage.py`:`PENDING = set()`,删 `test_pending_scripts_actually_pending`(已无意义),`test_wired_scripts_use_gate` 的 `if p.name in PENDING: continue` 删掉。跑全量回归:

Run: `venv/bin/python -m unittest discover tests 2>&1 | tail -3`
Expected: OK

```bash
git add tests/test_validator_coverage.py
git commit -m "test: 闸门覆盖 19/19 — 删除 PENDING 豁免, '无例外'机制化完成"
```

---

### Task 12: 2026-05 跨账本观察跑(只观察不阻断)

PR-A 验收线之一(spec §11):用封存月数据实测统计账 vs 凭证账的真实差异底数(时间语义对齐度、外卖勾稽符号),产出基线报告。封存语义允许只读观察(spec §6)。

**Files:**
- Create: `scripts/adhoc/audit_cross_ledger_202605.py`(沿用 adhoc audit 命名惯例)
- Create: `docs/audit/2026-06-cross-ledger-baseline.md`(跑完后写)

- [ ] **Step 1: 写观察脚本**

```python
#!/usr/bin/env python3
"""跨账本观察跑: 统计账 vs 凭证账, 2026-05 全量, 只观察不阻断.

产出: console 报告 (重定向归档到 docs/audit/2026-06-cross-ledger-baseline.md 的原始数据节)
回答 spec §11 PR-A 验收线的两个校准问题:
  1. sp.complete_time vs sb.finish_time 的时间语义差多大 (跨账本 qty/gross delta 分布)
  2. 外卖订单勾稽 platform_total vs item_sum 的真实匹配率

用法: venv/bin/python scripts/adhoc/audit_cross_ledger_202605.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from semantic.dimensions.time import month_to_ts_range          # noqa: E402
from semantic.entities.order_line import order_line_cte         # noqa: E402
from semantic.entities.sale_event import sale_event_cte         # noqa: E402
from semantic.entities.takeout_tieout import takeout_tieout_cte # noqa: E402
from semantic.validators import check, print_result             # noqa: E402
from semantic.validators.cross_ledger import build_cross_ledger_rows  # noqa: E402
from semantic.validators.identities import (                    # noqa: E402
    CROSS_LEDGER_IDENTITIES, TAKEOUT_TIEOUT_IDENTITIES)
from utils.report_engine import ReportEngine                    # noqa: E402

MONTH = "2026-05"

STAT_SQL = ("WITH " + sale_event_cte()
            + " SELECT item_uuid, SUM(qty) AS qty,"
              " SUM(gross_amount) AS gross_amount"
              " FROM sale_event GROUP BY item_uuid")
VOUCHER_SQL = "WITH " + order_line_cte() + " SELECT * FROM order_line"
TIEOUT_SQL = "WITH " + takeout_tieout_cte() + " SELECT * FROM takeout_tieout"


def main() -> int:
    engine = ReportEngine()
    start_ts, end_ts = month_to_ts_range(MONTH)
    # merchants 加载方式抄 bq_reports/profit_margin_report.py 的 main() —
    # resources/config.yaml 的活配置, 60 家全量
    ...  # 实施时按 profit_margin 的 merchants 加载 + engine.query 三连查
    # 对每家店: build_cross_ledger_rows(stat, voucher) → check(CROSS_LEDGER_IDENTITIES)
    # 汇总打印: 每店 qty 完全匹配率 / gross delta 分布 (P50/P95/max) /
    #           外卖勾稽匹配率 / TOP 20 最大差异 (store, item, stat vs voucher)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

(查询装配的 `...` 处抄 `profit_margin_report.py` main() 的 merchants 加载与 `engine.query(...)` 调用形态——实施者照抄现有模式填实,这不是留白而是"跟现网一致"的指令;脚本是一次性 adhoc,不进测试网。)

观察跑还要覆盖两件事:

1. **支付勾稽实测**:每店多查一条 `SELECT SUM(payment_amount) FROM ttpos_sale_bill WHERE status=1 AND delete_time=0 AND finish_time ∈ 窗口`,与统计账 `SUM(actual_amount)` 构成 `{"payment_amount_sum": ..., "stat_actual_sum": ...}` 行,跑 `[PAYMENT_TIEOUT_IDENTITY]`,差异分布写进基线报告(决定技术债 ② 何时还)。
2. **缺表预检**:30+ 家店没 `ttpos_takeout_order`(pitfalls §4.2)。先用 `bq_reports/utils/bq_exporter.py:675-678` 的 INFORMATION_SCHEMA UNION ALL 预检模式拿到"有表店清单",外卖勾稽只查有表店;无表店在报告里列为显式 N/A 节,严禁静默跳过(spec §8)。

- [ ] **Step 2: 跑观察(真 BQ,约几分钟)**

Run: `venv/bin/python scripts/adhoc/audit_cross_ledger_202605.py 2>&1 | tee /tmp/cross_ledger_obs.txt; tail -40 /tmp/cross_ledger_obs.txt`
Expected: 每店匹配率汇总 + delta 分布 + TOP 差异清单,无异常栈

- [ ] **Step 3: 写基线报告 + 升级决策**

`docs/audit/2026-06-cross-ledger-baseline.md` 结构:

```markdown
# 跨账本互证基线 (2026-05 观察跑)

## 结论(三选一,按数据勾选)
- [ ] qty 互证匹配率 100% → CROSS_LEDGER_QTY 进闸门 (加入 FULL_IDENTITIES)
- [ ] 存在稳定可解释差异(时间语义/退款时点)→ 修 order_line 口径后复跑
- [ ] 存在不可解释差异 → 开专项排查, CROSS_LEDGER 维持观察模式

## 数据
(粘贴 /tmp/cross_ledger_obs.txt 汇总节)

## 外卖勾稽
匹配率 / merchant 字段是否仍恒 0 / TAKEOUT_TIEOUT 是否解除封顶 🟡 的建议
```

- [ ] **Step 4: 按结论执行升级(若勾第一项)**

`identities.py`:`FULL_IDENTITIES` 加 `+ CROSS_LEDGER_IDENTITIES`,同时报表脚本要给 gate 喂 cross_ledger rows——这步若数据不支持就不做,**把不做的原因写进基线报告**,留给 PR-B 复跑。

- [ ] **Step 5: Commit**

```bash
git add scripts/adhoc/audit_cross_ledger_202605.py docs/audit/2026-06-cross-ledger-baseline.md
git commit -m "chore(audit): 跨账本观察跑 2026-05 基线 — CROSS_LEDGER 升级决策记录"
```

---

### Task 13: 文档收口(CLAUDE.md / spec 勘误 / gap doc)

**Files:**
- Modify: `CLAUDE.md`(第 3 节改写 + 新增技术债节)
- Modify: `docs/superpowers/specs/2026-06-12-zero-tolerance-design.md`(merchant 字段归宿勘误注记)
- Modify: `docs/pnl-accounting-standards-gap.md`(追加一行)

- [ ] **Step 1: CLAUDE.md 第 3 节改写**

标题"### 3. 导出必须接校验器(无例外)"下的正文,把"最小集成(4 行 + 一个字典构造)"代码块替换为闸门版:

```python
from semantic.validators.gate import validate_and_gate, add_watermark_sheet_xlsxwriter
from semantic.validators.identities import FULL_IDENTITIES

outcome = validate_and_gate(check_rows, FULL_IDENTITIES,
                            force=args.force, report_name="my_report",
                            row_label=lambda r: f"店 {r['store_num']}")
# 有 🔴 且无 --force → 已在函数内 exit 2, 不产出文件
if outcome.needs_watermark:   # --force 强制导出
    add_watermark_sheet_xlsxwriter(wb, outcome.watermark_lines())
```

并在该节末尾加:

```markdown
**"无例外"是机制不是口号**:`tests/test_validator_coverage.py` AST 扫描
`bq_reports/*.py`,不接 `validate_and_gate` 的脚本直接挂测试。
校验失败默认 exit 2 不产出文件;`--force` 强制导出的文件首页带红色水印
"⚠️ 本表未通过零容差校验",不得对外交付。
非销售类导出(BOM/菜单)用 `make_required_fields_identity` /
`make_unique_key_identity` 基线,不许裸奔。
```

- [ ] **Step 2: CLAUDE.md 加技术债节(第 5 节之后)**

```markdown
### 6. 技术债清单(零容差改造,spec: docs/superpowers/specs/2026-06-12-zero-tolerance-design.md)

| # | 债 | 还债条件 |
|---|---|---|
| ② | 支付勾稽 (bill.payment_amount vs 统计账实收) 封顶 🟡 未转红线 | service_fee/tax_fee/整单折扣口径用真实数据校准后 |
| ③ | 外卖平台侧退款不在恒等式内 | 属对账桥范围,子项目 D 接平台对账单后 |
| ④ | sale_event / sale_line 双轨并存 | CROSS_LEDGER 证明等价后合并(原 Phase 2 承诺) |
| ⑤ | pnl_statement 只接基线校验,销售恒等式待 P&L 行字段对齐 | PR-B 整数化时一并对齐 |
| ⑥ | CROSS_LEDGER 是否进闸门 | 见 docs/audit/2026-06-cross-ledger-baseline.md 结论 |

(技术债 ① "2026-05 前旧口径封存" 在 PR-B 落 month guard 时一并写入。)
```

- [ ] **Step 3: spec 勘误注记**

spec §5 A2 段末加:

> **实施勘误(PR-A Task 6):**merchant_charge_fee/merchant_discount 是订单级字段,JOIN 到 item 粒度的 sale_event 会按订单内 item 数重复计数。实际落点为订单粒度的 `takeout_tieout` CTE + `TAKEOUT_TIEOUT_IDENTITY`(封顶 🟡 待校准)。

- [ ] **Step 4: gap doc 追加**

`docs/pnl-accounting-standards-gap.md` "相关文档"节加一行:

```markdown
- [2026-06-12-zero-tolerance-design.md](./superpowers/specs/2026-06-12-zero-tolerance-design.md) — 内部一致性已零容差改造(跨账本互证/导出闸门);本文件的法定口径 gap 不受影响,仍待客户财务输入
```

- [ ] **Step 5: 全量回归 + Commit**

Run: `venv/bin/python -m unittest discover tests 2>&1 | tail -3`
Expected: OK

```bash
git add CLAUDE.md docs/superpowers/specs/2026-06-12-zero-tolerance-design.md docs/pnl-accounting-standards-gap.md
git commit -m "docs: 零容差 PR-A 收口 — CLAUDE.md 闸门机制化 + 技术债清单 + spec 勘误"
```

---

## PR-A 完成定义(对照 spec §11 验收线)

- [ ] 扰动测试全绿(恒等式可证伪)— Task 1/3/5/6
- [ ] 19 个报表脚本全接闸门,PENDING 名单清空 — Task 8-11
- [ ] 闸门生效:must_fix 阻断 exit 2 / --force 水印 — Task 7 + Task 9 Step 6 冒烟
- [ ] 2026-05 跨账本差异基线报告归档,CROSS_LEDGER 升级决策有记录 — Task 12
- [ ] CLAUDE.md / spec / gap doc 收口 — Task 13
- [ ] `venv/bin/python -m unittest discover tests` 全绿

## 不在 PR-A(防 scope creep)

- 整数化/萨当(PR-B)、month guard 与技术债 ①(PR-B)、尾差记账与 `_money_classify` 收零(PR-C)、对账桥产品化与口径注册表(子项目 D)
