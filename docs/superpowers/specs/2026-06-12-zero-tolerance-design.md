# 零容差数据治理 — 设计文档

> 日期:2026-06-12
> 状态:已与需求方逐节确认,待实施
> 范围:子项目 A(恒等式为真)+ B(金额整数化)+ C(尾差记账),按依赖序三个 PR 落地

## 1. 背景与问题

本仓库的数据口径体系借了复式记账的"守恒"精神(总量拆互斥完备桶 + 导出跑恒等式校验),
但审计发现"严格"二字有实打实的缺口,其中三条直接让现有校验失去检测力:

1. **销量恒等式是循环的、永真的**。三个接了校验器的脚本里 `net_qty` 全部用
   `qty − 其他桶` 减法推导(`profit_margin_report.py:1096`、
   `profit_by_price_report.py:737`、`report_sales_period_bq.py:689`),
   LHS 减出来的数加回去比 LHS,delta 代数恒为 0,查不出任何数据漂移。
2. **外卖侧金额恒等式平凡成立**。`sale_event.py` 把外卖 refund/free/give/discount
   硬编码 0,恒等式在外卖渠道等于没校验。
3. **校验覆盖率名不符实**。20 个报表脚本只有 3 个接了校验器,CLAUDE.md 的
   "无例外"没有机制保障。

另有两处口径/实现缺陷:

4. `cancelled_amount` 被排除在金额恒等式外(销量恒等式却含 `cancelled_qty`),守恒不闭合。
5. `reconciliation/base.py:130` `classify_money_severity` 的 base=0 漏洞:
   external 侧为 0 时 rel=0.0,走 `or rel < negligible_rel` 分支,任意大差额被判 NEGLIGIBLE。

容忍带本身也有存在原因:金额走浮点、订单级金额摊到行级有分摊尾差。
**零容差不是把阈值改成 0,而是先消灭残差来源、再给消灭不掉的残差立账。**

## 2. 已确认的决策

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 范围 | A+B+C 全做,拆三个 PR 按依赖序落地(方案一),B 切换月借双跑验收 |
| 2 | 历史数据 | **封存不重跑**。2026-05 及之前 = 旧浮点口径,永不重算;写备注/文档/CLAUDE.md,标技术债 |
| 3 | 封存线 | 2026-05 / 2026-06 之交。2026-06 起走零容差引擎 |
| 4 | 校验覆盖 | **全部 20 个报表脚本无例外**("每个给出去的都要可靠,不是玩具"),配结构性测试机制化 |
| 5 | 校验失败行为 | **硬阻断 + `--force` 逃生口**:默认不出文件;强制导出时 Excel 打水印 + console 记录 |
| 6 | 会计账目 | **本次不搞**(无科目/分录/总账)。只借"独立来源互证"原理。升级触发器见 §8 |

## 3. 关键发现:第二本账已存在

`net_qty` 的独立测量不需要 hack——ttpos 在 BQ 里有两套**不同代码路径写入**的数据:

- **统计账**:`ttpos_statistics_product`(现有管线唯一依赖,ttpos 后端预聚合)
- **凭证账**:`ttpos_sale_bill → ttpos_sale_order → ttpos_sale_order_product`
  (订单明细:`num`/`sale_price`/`price`/`total_price`/`discount_fee`;
  bill 级有 `payment_amount`)

恒等式循环问题的正解 = 升级为**跨账本互证**:统计账每个桶 vs 凭证账独立算出的同名值,
delta 必须为 0。顺带部分解决 gap 文档的 Gap 4(凭证级追溯)。

外卖侧边界(调研结论):

- 平台侧退款不进 ttpos,属对账桥(跨系统对账)范围,**不在内部恒等式内**(技术债 ③)
- ttpos 内可做:接入 `merchant_charge_fee`/`merchant_discount` 真实字段
  (华莱士当前为 0,业务开启时恒等式立刻响,而非静默偏离——pitfalls §5.1 点名的雷)
- 外卖自有跨口径检查:item 级 `SUM(toi.price×quantity)` vs 订单级 `platform_total`
- 30+ 家店没 `ttpos_takeout_order` 表(pitfalls §4.2),校验层必须显式 N/A,严禁静默当 0

## 4. 总体架构

三个子项目 = 三个独立合入的 PR 序列,每步合入后系统都可交付。改动集中四处:

```
semantic/
├── entities/order_line.py        # 新:凭证账 CTE (bill→order→order_product)
├── validators/
│   ├── identities.py             # 改:新增 CROSS_LEDGER / GROSS 家族;旧销量恒等式降级标注
│   ├── core.py                   # 不动(内核已够用)
│   └── gate.py                   # 新:导出闸门 (硬阻断 + --force 水印)
├── allocations.py                # 新(C 阶段):最大余额法分摊
utils/report_engine.py            # 改:内置 validate_and_gate(),20 个脚本统一接法
tests/test_validator_coverage.py  # 新:结构性检查——报表脚本不接校验器就挂测试
```

## 5. 子项目 A:让恒等式为真

**A1 凭证账实体 + 跨账本恒等式(核心)。**
新建 `order_line` CTE 读凭证账三表(`delete_time=0`、bill `status=1`),
(store, item) 粒度独立算 qty 和 gross。新恒等式家族 `CROSS_LEDGER_IDENTITIES`:

- 统计账 `qty` == 凭证账 `SUM(num)` —— qty 类立即零容差
- 统计账 `sales_price` == 凭证账 `SUM(sale_price×num)` —— 金额先沿用容忍带,B 后收零
- bill 粒度支付勾稽:`SUM(payment_amount)` vs 统计账实收 —— 以 NEEDS_REVIEW 级上线,
  service_fee/tax_fee/整单折扣口径映射拿真实数据校准后再转红线(技术债 ②)

**A2 旧恒等式降级 + 外卖去平凡化。**
`SALES_QTY_IDENTITY` 降级标注为"定义式守卫"(只防字段缺失/schema 漂移,
不再宣称是对账),代码备注指向 CROSS_LEDGER;真实检测力由 A1 接管。
外卖侧接 `merchant_charge_fee`/`merchant_discount` 真实字段;
新增 item 级 vs `platform_total` 订单级互证;缺表店显式 N/A 行。

> **实施勘误(PR-A Task 6):**merchant_charge_fee/merchant_discount 是订单级字段,JOIN 到 item 粒度的 sale_event 会按订单内 item 数重复计数。实际落点为订单粒度的 `takeout_tieout` CTE + `TAKEOUT_TIEOUT_IDENTITY`(封顶 🟡;2026-05 实测两字段恒 0,勾稽匹配 99.996%)。

**A3 守恒闭环 + 漏洞修复。**
新增 GROSS 恒等式(`gross = sales_price + cancelled_amount`)纳入取消金额,
消掉销量/金额恒等式的口径不对称;修 base=0 漏洞
(abs 与 rel 改为须同时满足才 NEGLIGIBLE)。

**A4 导出闸门。**
`gate.py`:`has_must_fix` → 不写文件、exit 2;`--force` → 写文件,
Excel 首 sheet 顶部插红色水印行"⚠️ 本表未通过零容差校验 (强制导出于 …)" +
console 记录。接入点在 `report_engine` 写盘步骤,20 个脚本自动获得。

**A5 全覆盖 + 防回归。**
20 个脚本全部走 `validate_and_gate()`;AST 结构性测试不接就挂;
property 扰动测试(见 §7)防循环恒等式回归。

## 6. 子项目 B(整数化)与 C(尾差记账)

### B:金额整数化(萨当)

- **唯一舍入点原则**:只在 CTE 输出层舍入一次,金额
  `CAST(ROUND(x * 100) AS INT64)` 转萨当(源字段 `decimal(12,2)`,×100 理论无损,
  ROUND 吃掉 BQ 浮点表示误差)
- Python 聚合层全程 `int`;比率(毛利率/退款率)是纯展示值,保持 float,不进恒等式
- Excel writer 是唯一"萨当→元"转换点,落盘最后一步 `/100`
- 逐行乘积求和型金额恒等式收紧到 `delta == 0`;分摊型留给 C
- **month guard**:`--month` ≤ 2026-05 拒跑,错误信息给归档文件路径,不留重跑口子。
  封存的语义是**不重新产出交付物**;只读的对账/审计查询(如 PR-A 的观察跑)不受限
- **双跑验收**:2026-06 真实数据新旧引擎各跑一次,逐 (store, item, 指标) diff
  报告归档为验收证据,然后**删除旧浮点路径**,不养两套代码

> **实施记录(PR-B,2026-06-13)。** 实际落地与设计一致,补充三点:
> - **整数化边界明确为语义层管线**(见 CLAUDE.md「整数化边界声明」);手写 SQL 报表
>   (bq_exporter/standalone 系)不在边界内,走基线恒等式。COGS/费率/利润/比率为估算域,
>   保持 float,交易金额进入估算域前在边界处 `/100` 一次(集中且注释)。
> - **双跑用快照前置策略**:整数化为就地修改,无并行引擎;Task 6 在整数化前 dump 浮点基准
>   (`exports/.dual_run/float_baseline_202606.json`,gitignore),Task 8 整数引擎复跑 diff
>   (`docs/audit/2026-06-dual-run-integerization.md`)。验收结论:**qty 冻结子集 21,205 行
>   零金额漂移 >0.01 THB**,超容差行全部归因为活跃月新销售(非缩放误差,经第三时点对抗复跑证伪)。
>   "删除旧浮点路径"由快照前置满足——无第二引擎存在。
> - **跨账本互证未达零容差**(技术债 ⑥,决策分支 c):套餐子行修复后 qty 匹配 31.5%→45.5%
>   (dine-only 93.2%),CROSS_LEDGER 维持观察 bundle,不进闸门;sum 型金额恒等式
>   (AMOUNT/GROSS)已收 `delta == 0`,2026-06 真数据全绿。

### C:尾差记账,然后真零容差

- `semantic/allocations.py`:最大余额法 `allocate(total_satang, weights) -> list[int]`,
  数学保证 `sum(parts) == total`。所有订单级金额摊行
  (`member_discount_fee`/`custom_discount_fee`/`service_fee`/`tax_fee`)统一走它
- **`rounding_residual` 桶**:跨账本比对中,ttpos 两条写入路径各自舍入产生的
  稳定 ±1 萨当级系统尾差(别人算的,消灭不了)——实测确认后立桶显式记账,
  恒等式带上该桶后 `delta == 0`。残差从"容忍带噪音"变成"报表可见科目"
- **终态**:`_money_classify` 删除容忍带,全部金额恒等式 `delta == 0`;
  `NEEDS_REVIEW` 仅保留给 sanity band 和未转红线的支付勾稽(它们本来就不是恒等式)

> **实施勘误(PR-C,2026-06-13)。** PR-B 实测推翻了本节字面 C 的三项前提,**C 的"尾差记账"未做,实际交付为凭证账全渠道覆盖**:
> - **`allocate()` 最大余额法 — 未做(无消费者)**:整数化后参与零容差的恒等式(AMOUNT/GROSS)都是逐行乘积求和,不涉及订单级金额摊到行级。member_discount_fee 等订单级折扣当前不进任何 sum 型恒等式,建分摊函数是 YAGNI。
> - **`rounding_residual` 桶 — 未做(尾差被结构差异淹没)**:CROSS_LEDGER 现 qty 89.5%,残余 10.5% 是后端写入路径不对称等**结构性差异**,不是 ±1 萨当舍入尾差,立桶无落点。
> - **删 `_money_classify` — 未做(原理上做不到 delta==0)**:它服务的 CROSS_LEDGER_GROSS(观察模式)、TAKEOUT/PAYMENT_TIEOUT(封顶🟡 口径未校准)是跨账本/跨口径对账,非 sum 型恒等式,delta==0 在原理上不可达(见 §8.5 共因错误/口径选择残余风险)。
> - **实际做了(PR-C)**:`order_line` 加外卖凭证账路径,凭证账从 dine-only 扩到全渠道,跨账本 qty 45.5%→89.5%(`docs/audit/2026-06-cross-ledger-baseline.md` PR-C 章节)。真零容差终态的剩余障碍 = ttpos 后端 sp/sop 写入可靠性(后端问题,上交,非 BQ 范围),非本设计可关闭。

## 7. 测试策略

- **存量不破**:现有 150+ 测试全程绿,每个 PR 合入前提
- **Property 扰动测试**(A 阶段第一个写):对注册表每条恒等式,在通过的 fixture 行上
  逐个扰动其引用字段(qty 类 +1、金额类 +1 个最小单位:A 阶段 0.01 元,B 后 1 萨当),
  断言对应恒等式必须 fire。
  泛化实现,新恒等式零额外测试代码。该测试今天就能抓出"销量恒等式永真"
- **结构性覆盖测试**:AST 扫 `bq_reports/*.py`,断言全部走 `validate_and_gate()`
- **闸门行为测试**:has_must_fix → 无文件 + exit 2;`--force` → 有文件 + 水印 + 记录
- **分摊精确性测试**:`allocate()` property test,任意 total/weights 下
  `sum(parts) == total`(含负数金额、零权重边界)
- **双跑 diff harness**:一次性脚本,产出差异清单归档进 `docs/`,不进长期代码

## 8. 错误处理

| 场景 | 行为 |
|---|---|
| 店缺 takeout 表 | 显式 N/A 行 + console 黄色提示,严禁静默当 0 |
| 凭证账缺数据但统计账有 | 跨账本恒等式 MUST_FIX,console 文案区分"未经互证" vs "数字错了" |
| 冻结月份 | month guard 拒跑,给归档文件路径 |
| schema 漂移 | 沿用 core.py 现有行为:MUST_FIX 冒出,不静默跳过 |

## 8.5 可靠性保证边界(对外承诺口径)

**"绝对可靠"不存在,可承诺的是有明确边界、逐层加强的保证。**

本设计(A+B+C)买到的是**条件性数学保证**:只要 BQ 源表正确,输出的每个数字正确
——管线内无声错误归零,任何错误要么不存在、要么以红色/水印/N/A 显式可见。

三类残余风险,任何内部校验在原理上覆盖不了:

1. **共因错误**:统计账/凭证账同源于 ttpos 同一次收银事件,录入错误或上游 bug
   会一致地污染两本账,互证照样全绿。同源账本的独立性是打折的;
   真独立的来源在系统外(银行流水、平台对账单)。
2. **不在系统里的数据**:平台侧退款、缺 takeout 表的店、同步延迟——
   缺失可以显式可见,但不能凭空补齐。
3. **口径选择**:数字算得分毫不差,仍可能答非所问(营业额≠支付净额≠实际到账)。
   靠对账桥与口径文档,不靠校验器。

可靠性阶梯(本仓库的演进方向):

```
第 1 层  管线数学保证   ← 本次 A+B+C:源对则出对,无声错误归零
第 2 层  外部独立互证   ← 对账桥/最小账本:银行流水、平台对账单(真独立)
第 3 层  物理世界锚定   ← 盘点:ttpos_stock_reconciliation vs BOM 展开消耗;
                          钱守恒 + 物守恒
```

走完三层后对客户的最强承诺话术:
**"每个数字都经过管线数学校验 + 至少一个独立外部来源互证,未经互证的数字显式标注。"**

## 9. Non-goals 与升级路线(何时该搞真记账)

**本次不做**:会计科目表、借贷分录、总账、法定财报对接(见
`docs/pnl-accounting-standards-gap.md`,卡在客户财务给科目表)。
本次只借复式记账的一条原理:**一个数要可信,必须有第二个独立来源互证**。

**升级到"最小可用账本"的判断标准**:当问题从"某时点的数对不对"
变成"钱在状态之间怎么流转",交叉检验原理上覆盖不了,需要科目+分录。触发器
(先到先触发):

1. **平台对账单接入常态化**(客户给 Grab/LINE MAN/Shopee 对账单样本之时)——
   营业额→应收平台款→扣佣→到账的状态流,每笔差额就是一个科目
2. **会员储值/礼品卡上量**——充值是负债,消费才转收入;
   `ttpos_member_recharge_order`/`balance` 已在记,报表层缺"负债→收入"转换账
3. **客户财务给出 Chart of Accounts**——法定口径对接启动

届时形态是"最小可用账本":四五个科目(营业额/应收平台款/平台费用/到账/储值负债),
分录 = 对账桥每一行,不是完整总账。跨期退款(Gap 1)和多主体合并(Gap 5)同期解决。

### 子项目 D(已决策:单独立项,不在本轮)— 对账桥产品化 + 口径注册表

本轮 A+B+C 之后、最小账本之前的衔接项目,单独走一轮 brainstorm → spec:

- **对账桥产品化**:把 5 月对账排查沉淀的 adhoc audit 脚本(commit `f5f6d56`/`14c719b`)
  套上 `semantic/reconciliation/` 现成的 Check 协议,升级为常驻 Check。
  桥本身守恒:`口径A − 口径B = Σ(BridgeItem)`,残差零容差,解释不掉进显式
  `unexplained` 桶并标 MUST_FIX。每月导出自动跑,交付物多一个"对账桥"sheet
  (营业额→支付净额→到账逐项 walk),对账从被动响应变主动披露。
  到账段依赖客户提供平台对账单,给之前显式标"待外部数据"。
- **口径注册表**:每个指标一条结构化记录(名称/业务定义/SQL 来源/参与的恒等式/
  对账对象)。`metrics-catalog.md` 与交付物的"口径说明"sheet 从注册表生成
  (复用 sync-docs 模式);结构性测试:报表 YAML 出现的指标列必须在注册表登记。
  注册表同时是最小账本的前置(科目 = 注册过的口径)。

## 10. 文档与技术债落点

| 落点 | 内容 |
|---|---|
| 本文件 | 设计真源 |
| `CLAUDE.md` 第 3 节改写 | "无例外"从口号改为机制(结构性测试 + 闸门 + `--force` 语义) |
| `CLAUDE.md` 新增"历史封存"节 | 封存线 2026-05/06、month guard、新旧数字不可逐分比较 |
| `CLAUDE.md` 技术债清单(新增节) | ① 2026-05 前旧口径封存永不重算 ② 支付勾稽未转红线(待真实数据校准) ③ 外卖平台侧退款不在恒等式内(对账桥范围) ④ sale_event/sale_line 双轨待 A1 证明等价后合并 |
| `docs/pnl-accounting-standards-gap.md` | 追加一行指向本 spec:内部一致性已零容差,法定口径 gap 不变 |
| `SALES_QTY_IDENTITY` 代码备注 | "定义式守卫,非对账;检测力见 CROSS_LEDGER" |

## 11. 三个 PR 的验收线

- **PR-A**:扰动测试全绿(恒等式可证伪)+ 20 脚本全接 + 闸门生效 +
  用 2026-05 数据试跑跨账本对账出首份差异报告(只观察不阻断,摸真实底数)
- **PR-B**:双跑 diff 归档 + 旧浮点路径删除 + month guard 生效
- **PR-C**:`_money_classify` 收到 `delta == 0` + 全量恒等式在 2026-06 数据上
  零违反(或违反全部记账为 `rounding_residual`)
