# 零容差 PR-C(凭证账全渠道覆盖 + CROSS_LEDGER 现实定界)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development 或 executing-plans。Steps 用 checkbox。

**Goal:** 给凭证账 `order_line` 加外卖路径,使跨账本互证从 45.5%→89.5%;基于实测把 CROSS_LEDGER 的目标从幻想的 ≥99% 重定为结构天花板,并把残余根因(ttpos 后端写入路径不对称)清晰定界、上交。

**Architecture:** order_line CTE 加一个外卖 UNION ALL 分支(对齐 sale_event takeout 口径:state IN(10,20,30,40)、动态时间、`package_uuid>0`、萨当整数化);复跑观察更新基线;CROSS_LEDGER 维持观察模式(不进闸门),技术债 ⑥ 重写为诚实天花板。

**Tech Stack:** 同 PR-A/B(venv/unittest/BQ asia-southeast1)。

**Spec:** `docs/superpowers/specs/2026-06-12-zero-tolerance-design.md` §6 C;基线:`docs/audit/2026-06-cross-ledger-baseline.md`

---

## 范围重定位声明(为什么不做 spec §6 C 字面内容)

spec §6 C 设想"尾差记账(allocate 最大余额法)+ rounding_residual 桶 + 删 _money_classify 达真零容差终态"。**PR-B 的实测数据揭穿了这个设想的前提,以下三项不做,理由如下:**

1. **`allocate()` 最大余额法分摊 — 不做。** 设想用途是"订单级折扣(member_discount_fee 等)摊到行级"。但 PR-B 整数化后,参与零容差的恒等式(AMOUNT/GROSS)都是逐行乘积求和,不涉及订单级摊分。该函数**当前无消费者**,建它是 YAGNI。
2. **`rounding_residual` 尾差桶 — 不做。** 设想给跨账本两本账各自舍入的 ±1 萨当系统尾差立桶。但 CROSS_LEDGER 现 qty 仅 45.5%,尾差被套餐+外卖+写入路径的**结构性差异淹没**,不是当前主要矛盾。
3. **删 `_money_classify` 收全部金额恒等式 delta==0 — 不做。** 它服务的 CROSS_LEDGER_GROSS(观察模式)、TAKEOUT/PAYMENT_TIEOUT(封顶🟡 口径未校准)**原理上做不到 delta==0**(跨账本/跨口径对账,非 sum 型恒等式)。强删会让闸门误报。

**真正解锁零容差核心(跨账本互证)的下一步 = 技术债 ⑥**:凭证账补外卖路径。本 PR 做这个。它有数据目标(45.5%→实测 89.5%)、有 PR-C 调查实测根因、产出真价值(凭证账终于覆盖全渠道)。

## 调查实测结论(PR-C 地基,2026-06-13 真 BQ)

- **外卖凭证账源**:`ttpos_takeout_order_item` toi JOIN `ttpos_takeout_order` t。字段:`toi.ttpos_product_package_uuid`(item_uuid,`>0` 排未映射)、`toi.quantity`、`toi.price`、`toi.ttpos_product_type`(0=单品/1=套餐父,**均顶层,toi 无子行,无双计数**)、`t.order_state IN(10,20,30,40)`、动态时间(state=40 用 completed_time 否则 accepted_time)、`delete_time=0`。
- **实测提升**:加外卖分支后 2026-05 qty 全局 45.5%→**89.5%(+44pp)**;shop001/003 43%→96.9%。
- **时间语义假设推翻**:dine 残差非月界时间漂移(shop018 月界 0 跨窗行),真根因=**后端写入路径不对称**(sop 记录了 sp 漏记的销售)。**不改时间字段**(改了更糟)。
- **天花板**:~89.5%,残余 10.5%=结构性(后端 sp/sop 写入不对称 + 未映射商品 + 促销码路径),≥99% 不可达,后端问题 BQ 层不可解。

---

### Task 1: order_line 加外卖 UNION 分支

**Files:** `semantic/entities/order_line.py`、`tests/test_order_line.py`

- [ ] **Step 1: 失败测试** — `tests/test_order_line.py` 加:

```python
    def test_takeout_branch_present(self):
        # 凭证账外卖路径: ttpos_takeout_order_item, 对齐 sale_event takeout 口径
        self.assertIn("`p`.`d`.`ttpos_takeout_order_item` toi", self.sql)
        self.assertIn("toi.ttpos_product_package_uuid", self.sql)
        self.assertIn("t.order_state IN (10, 20, 30, 40)", self.sql)
        self.assertIn("UNION ALL", self.sql)

    def test_takeout_dynamic_time(self):
        # pitfalls §1.3: state=40 用 completed_time, 否则 accepted_time
        self.assertIn("t.order_state = 40 AND t.completed_time >= 1", self.sql)
        self.assertIn("t.order_state != 40 AND t.accepted_time >= 1", self.sql)

    def test_takeout_money_integerized(self):
        # 萨当整数化, 与 dine 分支一致 (PR-B 边界)
        self.assertIn("CAST(ROUND(SUM(toi.price * toi.quantity) * 100) AS INT64) AS voucher_gross", self.sql)
```

- [ ] **Step 2:** 跑 `venv/bin/python -m unittest tests.test_order_line -v` → 新断言 FAIL。

- [ ] **Step 3:** `order_line_cte()` 在 dine 分支(现有 SELECT … GROUP BY item_uuid)之后、CTE 闭合 `)` 之前,加外卖 UNION 分支。dine 分支当前结构是单个 SELECT;把它包成 `SELECT item_uuid, SUM(voucher_qty)… FROM (<dine SELECT> UNION ALL <takeout SELECT>) GROUP BY item_uuid`,或直接两个 SELECT UNION ALL 后外层 GROUP BY。采用**外层 GROUP BY 合并**(同 item 的 dine+takeout 合一行):

```python
def order_line_cte() -> str:
    return """order_line AS (
  SELECT
    item_uuid,
    SUM(voucher_qty) AS voucher_qty,
    SUM(voucher_gross) AS voucher_gross,
    SUM(voucher_net) AS voucher_net,
    SUM(voucher_discount) AS voucher_discount
  FROM (
    -- 堂食凭证账: sale_bill → sale_order → sale_order_product (排套餐子行 product_type=2)
    SELECT
      sop.product_package_uuid AS item_uuid,
      SUM(sop.num) AS voucher_qty,
      CAST(ROUND(SUM(sop.sale_price * sop.num) * 100) AS INT64) AS voucher_gross,
      CAST(ROUND(SUM(sop.total_price) * 100) AS INT64) AS voucher_net,
      CAST(ROUND(SUM(sop.discount_fee) * 100) AS INT64) AS voucher_discount
    FROM `{project}`.`{dataset}`.`ttpos_sale_order_product` sop
    JOIN `{project}`.`{dataset}`.`ttpos_sale_order` so
      ON so.uuid = sop.sale_order_uuid AND so.delete_time = 0
    JOIN `{project}`.`{dataset}`.`ttpos_sale_bill` sb
      ON sb.uuid = so.sale_bill_uuid AND sb.delete_time = 0
    WHERE sb.status = 1
      AND sb.finish_time >= {start_ts}
      AND sb.finish_time < {end_ts}
      AND sop.delete_time = 0
      AND sop.product_type != 2
    GROUP BY item_uuid

    UNION ALL

    -- 外卖凭证账: ttpos_takeout_order_item (toi 无子行, type 0/1 均顶层)
    SELECT
      toi.ttpos_product_package_uuid AS item_uuid,
      SUM(toi.quantity) AS voucher_qty,
      CAST(ROUND(SUM(toi.price * toi.quantity) * 100) AS INT64) AS voucher_gross,
      CAST(ROUND(SUM(toi.price * toi.quantity) * 100) AS INT64) AS voucher_net,
      CAST(0 AS INT64) AS voucher_discount
    FROM `{project}`.`{dataset}`.`ttpos_takeout_order_item` toi
    JOIN `{project}`.`{dataset}`.`ttpos_takeout_order` t
      ON t.uuid = toi.takeout_order_uuid AND t.delete_time = 0
    WHERE toi.delete_time = 0
      AND toi.ttpos_product_package_uuid > 0
      AND t.order_state IN (10, 20, 30, 40)
      AND t.accepted_time > 0
      AND (
        (t.order_state = 40 AND t.completed_time >= {start_ts} AND t.completed_time < {end_ts})
        OR (t.order_state != 40 AND t.accepted_time >= {start_ts} AND t.accepted_time < {end_ts})
      )
    GROUP BY item_uuid
  )
  GROUP BY item_uuid
)"""
```

docstring 更新:口径要点加"外卖路径: ttpos_takeout_order_item, 对齐 sale_event takeout 口径(state/动态时间/package_uuid>0);toi 的 ttpos_product_type 0=单品 1=套餐父均顶层, 无子行无双计数(实测含 type=1 时 shop003 96.9% vs 仅 type=0 的 71.7%)"。

⚠️ 注意 dine 分支金额列已是 PR-B 整数化版本(萨当 CAST ROUND ×100)——保持。voucher_qty 是数量不整数化。外层 GROUP BY 对已整数化的萨当列 SUM,精确。

- [ ] **Step 4:** 跑 `venv/bin/python -m unittest tests.test_order_line -v` → PASS;全量回归 `venv/bin/python -m unittest discover tests 2>&1 | tail -3` → 484 OK(无报表消费 order_line,改动隔离)。

- [ ] **Step 5: Commit**

```bash
git add semantic/entities/order_line.py tests/test_order_line.py
git commit -m "feat(semantic): order_line 加外卖凭证账路径 — 凭证账覆盖全渠道, 跨账本 qty 45.5%→89.5%"
```

---

### Task 2: 复跑观察 + 基线更新 + CROSS_LEDGER 定界

**Files:** `docs/audit/2026-06-cross-ledger-baseline.md`(追加 PR-C 章节)、`scripts/adhoc/audit_cross_ledger_202605.py`(若需输出增强,否则只跑)

- [ ] **Step 1: 复跑** — `venv/bin/python scripts/adhoc/audit_cross_ledger_202605.py 2>&1 | tee /tmp/cross_ledger_prc.txt`。预期 qty 全局 ≈89.5%(对照 PR-B 45.5%)。脚本从 order_line CTE 拼 SQL,自动带外卖路径。捕获:全局 qty 匹配率、dine/takeout 分渠道、最差店残差、残余结构分解。

- [ ] **Step 2: 更新基线** — `docs/audit/2026-06-cross-ledger-baseline.md` 追加 `## PR-C 复跑 (外卖路径, 2026-06-13)`:45.5%→实测%、推翻"时间语义"假设(真根因=后端写入路径不对称,shop018 月界 0 跨窗行)、天花板 ~89.5% + 残余三层(后端 sp/sop 写入不对称 / 未映射商品 / 促销码路径)、明确"≥99% 不可达, 后端问题 BQ 不可解, 需后端 sp 写入可靠性调查(本仓库范围外)"。

- [ ] **Step 3: CROSS_LEDGER 升级判定** — 决策:**维持观察模式,不进闸门**。理由写进基线报告:89.5% 的 10.5% 残差是后端不可控结构性差异,进 🔴 会天天误报、进 🟡 FULL_IDENTITIES 会给每次导出引入 ~10% 噪音稀释闸门可信度(零容差核心=响警报=真有错)。CROSS_LEDGER 留 adhoc 月度观察跑。**identities.py 不改**(CROSS_LEDGER 仍 standalone)。

- [ ] **Step 4: Commit**

```bash
git add docs/audit/2026-06-cross-ledger-baseline.md scripts/adhoc/
git commit -m "chore(audit): 跨账本复跑 — 外卖路径后 qty <实测>%; 推翻时间语义假设(真因=后端写入不对称); CROSS_LEDGER 维持观察(结构天花板)"
```

---

### Task 3: 文档收口

**Files:** `CLAUDE.md`(技术债 ⑥ 重写)、`docs/superpowers/specs/2026-06-12-zero-tolerance-design.md`(§6 C 实施记录)

- [ ] **Step 1: CLAUDE.md 技术债 ⑥ 重写** — 从"修 order_line 套餐口径 → 复跑 → 100% 后进闸门"改为:

```markdown
| ⑥ | CROSS_LEDGER 维持观察模式(决策分支 c): 凭证账已补外卖路径(PR-C), qty 45.5%→89.5%; 残余 10.5% 结构天花板 = ttpos 后端 sp/sop 写入路径不对称(凭证记录了统计漏记的销售)+ 未映射商品 + 促销码路径 | ≥99% 不可达且后端问题 BQ 不可解; 完全闭合需 ttpos 后端 sp 写入可靠性调查(本仓库范围外)。CROSS_LEDGER 不进闸门避免 ~10% 不可控噪音稀释闸门可信度 |
```

- [ ] **Step 2: spec §6 C 实施记录** — §6 C 节末加实施勘误块:说明 PR-C 实测推翻了字面 C 的三项前提(allocate 无消费者 / rounding_residual 被结构差异淹没 / _money_classify 服务的恒等式原理上做不到 delta==0),实际交付=凭证账全渠道覆盖(45.5%→89.5%),真零容差终态的剩余障碍是 ttpos 后端写入可靠性(上交,非 BQ 范围)。

- [ ] **Step 3:** 全量回归 484 OK + Commit

```bash
git add CLAUDE.md docs/superpowers/specs/2026-06-12-zero-tolerance-design.md
git commit -m "docs: 零容差 PR-C 收口 — 技术债⑥ 重定为结构天花板+后端根因, spec §6 C 实施勘误"
```

---

## PR-C 完成定义

- [ ] order_line 覆盖 dine+takeout 两渠道,测试断言外卖分支 + 动态时间 + 萨当整数化
- [ ] 复跑实测 qty 匹配率归档(预期 ≈89.5%),推翻时间语义假设、定界后端根因
- [ ] CROSS_LEDGER 升级判定:维持观察模式(结构天花板,不进闸门),有记录
- [ ] 技术债 ⑥ 重写为诚实天花板 + 后端根因上交;spec §6 C 实施勘误
- [ ] `venv/bin/python -m unittest discover tests` 全绿(484)

## 不在 PR-C(前提不成立 / 范围外)

- allocate() 最大余额法(无消费者)、rounding_residual 桶(尾差被结构差异淹没)、删 _money_classify(服务的恒等式原理上做不到 delta==0)、时间语义对齐(根因非时间)
- ttpos 后端 sp/sop 写入可靠性修复(BQ 范围外,上交后端)
- 双轨合并(技术债 ④ 后半)、对账桥/口径注册表(子项目 D)
