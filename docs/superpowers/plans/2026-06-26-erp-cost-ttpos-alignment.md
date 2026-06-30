# ERP 物料成本取数对齐 ttpos 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development(推荐)或 superpowers:executing-plans 按任务逐条实施。步骤用 checkbox(`- [ ]`)跟踪。

**Goal:** 让我们算 COGS 的物料单价层,端到端忠实复刻 ttpos 后端 `GetItemUnitCost`(价表 `Buying - Internal`、`Item Price.price_list_rate`、按 desired-UOM 选行、`PRLE-0003` 条件 margin、Item Tax 税率带),消除"公式对齐、取数没对齐"的系统性偏差,并加 anchor 防再漂。

**Architecture:** 采**方案 C = 忠实离线复刻 + ttpos 对账锚**。生产跑离线复刻(不依赖 ttpos 服务可用/VPN,沿用本仓 `ttpos_anchor.py` 的"跨 SQL 对账"哲学);另加一个独立读 ERP 真源、按 ttpos 算法复算的 anchor check,逐期比对、漂了就红。改动集中在三个文件 + 一个新 anchor,不动零容差恒等式数学,不重算封存月(≤2026-05)。

**Tech Stack:** Python 3 / `requests`(Frappe REST)/ 本仓 `semantic.resolvers` Resolver 框架 / `unittest`(对齐现有 `tests/test_erpnext_price.py`)。

---

## 路径修订(2026-06-26):sid 降为后备,主路径无 sid

**主路径(不需要 ERP sid):**
1. 改算法代码忠实化(价表常量、`PRLE-0003` 条件套、desired-UOM 不丢弃、税)—— 全在 ttpos 源码可抄,**纯代码**。
2. 套 ②`clean_bom.csv` **现有 `基价(原始)`** 重算成本(**不重拉 ERP**)。
3. 验证 = **Task 4.3 人工对 ttpos 后台成本报表**(同事 UI 导出,**不需 sid**)。

前提:信 clean_bom 现有 base = Buying-Internal 价、信反推 5% margin —— **由 Task 4.3 验掉,不是盲信**。
**sid 仅两种情况才需要**:(a) Task 4.3 验出系统性差 → 回 ERP 重拉 base;(b) 修 ① 实时管线。**故 Phase 0 探针 / Phase 5 由"前置门槛"降为"后备/可选"**,Phase 5 改为"重算现有 base"而非"重拉 ERP"。

**跨仓关联注释(drift 防护)—— 仅 analysis 侧(用户定:不动 ttpos 仓):**
- 本仓 Python forward-ref 指回 ttpos Go 真源行号:`erpnext_price.py` docstring 已有;在 Phase 1–3 各改动点给 `material_price.py` / `erpnext_api.py` 补注释(注明"复刻自 ttpos `item.go:107 GetItemUnitCost` / `business_cost_profit_erp_cost.go:103 priceList`,改前先看那边")。
- ttpos 侧不加注释(避免掺入他人 WIP 分支);drift 防护靠 analysis forward-ref + Task 4.3 人工对账。

---

## 已坐实的根因(源码级,无需 ERP 即确定)

对齐靶子 = ttpos 生产成本毛利路径 `main/app/service/business_cost_profit_erp_cost.go` → `ttpos-bmp/.../logic/stock/item.go::GetItemUnitCost`:

| 维度 | ttpos 真源(行号) | 我们现状(行号) | 偏差 |
|---|---|---|---|
| 价表 | `"Buying - Internal"`(business_cost_profit:103) | `erpnext_api` 默认 `"Standard Buying"`(erpnext_api.py:186);`.env ERPNEXT_PRICE_LIST=Standard Buying` | 取错表 |
| 字段 | `Item Price.price_list_rate`(item.go:151) | 403 时静默回退 `Item.last_purchase_rate`(erpnext_api.py:179-180) | 字段/口径都不同 |
| UOM 选行 | 按该物料 desired UOM(item.go:156、`preferItemUnitCost` 385-398) | `erpnext_api` 写死 `g>pc>nos` 优先级(207-238);消费侧 `material_price.py:150` **直接丢弃 `_uom`**,仅 `{MK01018:50}` 一条硬编码修正(material_price.py:24-26) | UOM 不匹配静默出错 |
| margin | `PRLE-0003` 规则,**条件适用**(buying & !disabled & Price & for_price_list 匹配 & 日期有效,item.go:279-296),rate 实时读 | `erpnext_price.py` 写死 `DEFAULT_BUYING_RULE=("Percentage",5.0)` 无条件套(erpnext_price.py:39、90) | 价表不匹配/规则停用时凭空 +5% |
| 税 | Item Tax 模板 + min/max net band,兜底 7%(item.go:332-348) | 每行 `适用税率%`(来源存疑),复刻只 `None→7`(erpnext_price.py:75-77) | 边界/模板税率可能错 |

公式形状(`calculateFinalItemUnitCost`/`applyItemUnitCostPricingRules`)已逐行核对 Go↔Python 一致 ✅。偏差**全在输入与条件**。

## 方案抉择(本计划选 C)

- **A 忠实离线复刻**:Python 完整复刻 GetItemUnitCost(价表/字段/UOM 选行/条件规则/税带),直读 ERP。保"离线、不依赖 ttpos 服务"。风险=未来 ttpos 改规则会再漂 → 由锚兜。
- **B 直接调 ttpos `GetItemUnitCost` RPC**:最真、零漂,但给离线分析管线引入 ttpos-bmp gRPC 运行时依赖(proto/鉴权/服务可用),违背本仓刻意的"跨 SQL 不跨进程"设计 → 否决为生产路径。
- **C(选)= A + ttpos 对账锚**:生产用 A;另起独立 anchor 复算 ERP 真源逐期比对,落地态用"独立读 ERP 按 ttpos 算法复算"(非 RPC)。与现有 `ttpos_anchor.py`(Net Sales)同构。

**方案排除(2026-06-26 已坐实,带证据):**
- ~~方案 B:调 ttpos `GetItemUnitCost` RPC~~ ❌ 导表脚本须先对接 ttpos 鉴权,太重(用户确认);违背本仓"不依赖 ttpos 服务可用/凭据/VPN"设计。
- ~~方案 4:读 BQ 物化成本~~ ❌ INFORMATION_SCHEMA 全扫 + 抽样实证:成本字段 `ttpos_material.price` / `ttpos_product_bom.purchase_price` / `ttpos_purchase_receipt_order_item.snapshot_price` **全 0%**;唯二有值的 `ttpos_product_bom.price` / `ttpos_sale_order_product_bom.price` 是**套餐售价 / 加料价**(149「欢乐套餐1」、35 `is_flavor_bom`),非物料成本。ttpos 成本 = RPC 现算 + 内部缓存,不落 BQ。
- **锚 oracle = 独立直读 ERP 原始 + ttpos 算法复算**(非 RPC):独立代码路径,非循环;"真等于 ttpos"那一锤靠 Task 4.3 一次性人工对 ttpos 后台报表。

## 影响边界 / 红线

- 改的是 COGS 物料单价口径 → 会改 2026-06 起的成本/毛利数。**封存月(≤2026-05)不重算**:所有重算入口先过 `semantic/dimensions/time.py::assert_month_not_frozen`。
- ERPNext 层 priority=50,会被客户外挂 `price_layers`(100+)/`uploaded_prices`(80)盖过。本计划只修 ERPNext 层本身;客户层覆盖时本层不生效(审计列 price_source 会显示真实命中层)。
- **本计划范围 = ①实时取数+成本复刻+锚(Phase 0–4)**。②`clean_bom.csv` 用修正后管线重灌(Phase 5)是**依赖 Phase 0 拿到 ERP 读权限的后续计划**,本计划只留接口与门,不展开。

## 文件结构

- 改 `bq_reports/utils/erpnext_api.py` — 价表默认 + 去静默回退 + UOM 按 desired 选行
- 改 `bom_pipeline/erpnext_price.py` — 去硬编码 5%,改读 `PRLE-0003` 条件套
- 改 `semantic/cogs/material_price.py` — ERPNext 层停止丢弃 UOM,UOM 不匹配判定为缺口
- 新 `semantic/reconciliation/checks/ttpos_cost_anchor.py` — ttpos 成本对账锚
- 新/改 `tests/test_erpnext_price.py`、`tests/test_cogs_layer.py`、`tests/test_ttpos_cost_anchor.py`
- 改 `resources/wallace.20260626/`(或 fixtures)落 Phase 0 ERP 真值快照

---

## Phase 0:ERP 真源落锤(拿到新 sid 后第一件事;gates Phase 5)

### Task 0.1:只读探针 + 落 ERP 真值 fixture

**Files:**
- Create: `tests/fixtures/erp_buying_internal_probe.json`(探针产出,人工核对后入库)
- 复用探针脚本(已在会话中验证可跑;不入库,inline 跑)

- [ ] **Step 1:换上能读 `Item Price` 的新 sid**

把 `/home/weifashi/hwt/analysis/.env` 的 `ERPNEXT_SID=` 换成同事账号 sid(浏览器登录 erp.ttpos.dev → Cookie `sid`)。

- [ ] **Step 2:跑三方对账探针**

Run:
```bash
cd /home/weifashi/hwt/analysis/.dev/worktree/workspace-tidy
/home/weifashi/hwt/analysis/venv/bin/python - <<'PY'
import sys, csv; sys.path.insert(0,".")
from dotenv import load_dotenv; load_dotenv("/home/weifashi/hwt/analysis/.env")
from bq_reports.utils import erpnext_api as E
rows=list(csv.DictReader(open("resources/wallace.20260626/clean_bom.csv",encoding="utf-8-sig")))
def f(x):
    try:return float(str(x).replace(",","").strip() or 0)
    except:return 0.0
samp,seen=[],set()
for r in rows:
    c=(r.get("物料编码") or "").strip()
    if c and c not in seen:
        seen.add(c); samp.append((c,(r.get("物料名称") or "")[:14],f(r.get("基价(原始)")),(r.get("单位") or "").strip(),(r.get("ERP_UOM") or "").strip()))
    if len(samp)>=12:break
codes=[s[0] for s in samp]
bi=E.load_erpnext_prices(price_list="Buying - Internal",item_codes=codes)
sb=E.load_erpnext_prices(price_list="Standard Buying",item_codes=codes)
for c,n,base,u,eu in samp:
    print(f"{n:14} 基价={base:9.4f} | BI={bi.get(c)} | SB={sb.get(c)}")
bu,auth=E._get_auth()
print("PRLE-0003:",E._api_get(bu,auth,"Pricing Rule",["name","margin_type","margin_rate_or_amount","for_price_list","buying","disable","valid_from","valid_upto"],filters=[["name","=","PRLE-0003"]],limit=1))
PY
```
Expected:每物料拿到 `Buying - Internal` 的 (rate, uom);PRLE-0003 的真实 margin_rate / for_price_list / 启用状态。

- [ ] **Step 3:落 fixture + 记录判定**

把 BI 价、PRLE-0003 字段、Item Tax 抽样写入 `tests/fixtures/erp_buying_internal_probe.json`(给 Phase 1–4 测试当真值锚)。**记录三个事实**:(a) BI vs csv 基价差多少;(b) PRLE-0003 实际 margin 与 for_price_list;(c) 12 物料里 BI 缺 desired-UOM 行的有几个。

- [ ] **Step 4:Commit**

```bash
git add tests/fixtures/erp_buying_internal_probe.json
git commit -m "test(erp): 落 Buying-Internal/PRLE-0003 真值 fixture(对齐探针)"
```

> ⚠️ 若新 sid 仍全 403:转去查 `erp.ttpos.dev` 多租户 site-code 头(Go `WithSiteCode`,main/app/service/rpc/erp/item.go:38),给 `_api_get` 补 header,再回此 Task。

---

## Phase 1:取数对齐(erpnext_api.py)

### Task 1.1:价表默认改为 `Buying - Internal`,去掉静默 last_purchase_rate 回退

**Files:**
- Modify: `bq_reports/utils/erpnext_api.py:160-241`(`load_erpnext_prices`)
- Test: `tests/test_erpnext_api_pricelist.py`(新建)

- [ ] **Step 1:写失败测试**

```python
# tests/test_erpnext_api_pricelist.py
import unittest
from bq_reports.utils import erpnext_api as E

class TestPriceListDefault(unittest.TestCase):
    def test_default_price_list_is_buying_internal(self):
        # 不传 price_list 且 env 未设时,默认应为 ttpos 成本毛利口径价表
        self.assertEqual(E.COST_PROFIT_PRICE_LIST, "Buying - Internal")

    def test_last_purchase_fallback_is_not_silent(self):
        # 静默把 last_purchase_rate 当 Item Price 会引入隐性口径漂移:
        # 必须显式开关 + 返回带 source 标记,默认关闭
        self.assertFalse(E.ALLOW_LAST_PURCHASE_FALLBACK_DEFAULT)
```

- [ ] **Step 2:跑测试确认失败**

Run: `/home/weifashi/hwt/analysis/venv/bin/python -m unittest tests.test_erpnext_api_pricelist -v`
Expected: FAIL(`COST_PROFIT_PRICE_LIST` 未定义)

- [ ] **Step 3:实现**

在 `erpnext_api.py` 顶部加常量,并把 `load_erpnext_prices` 默认价表改为 `COST_PROFIT_PRICE_LIST`;`last_purchase_rate` 路径改为仅在显式传 `allow_last_purchase=True` 时启用,且日志 WARNING 标注口径降级:

```python
# 对齐 ttpos main/app/service/business_cost_profit_erp_cost.go:103
COST_PROFIT_PRICE_LIST = "Buying - Internal"
ALLOW_LAST_PURCHASE_FALLBACK_DEFAULT = False

# load_erpnext_prices 签名加 allow_last_purchase=False
def load_erpnext_prices(price_list=None, item_codes=None, sid=None, allow_last_purchase=ALLOW_LAST_PURCHASE_FALLBACK_DEFAULT):
    if os.environ.get("ERPNEXT_PRICE_SOURCE","").strip().lower() == "last_purchase_rate":
        if not allow_last_purchase:
            raise RuntimeError(
                "ERPNEXT_PRICE_SOURCE=last_purchase_rate 会把'最近采购价'冒充 Item Price,"
                "口径偏离 ttpos GetItemUnitCost。如确需降级请显式 allow_last_purchase=True。")
        print("[ERPNext API] ⚠️ 口径降级: 用 Item.last_purchase_rate 代替 Item Price(非 ttpos 口径)")
        return load_erpnext_item_last_purchase(item_codes=item_codes, sid=sid)
    ...
    price_list = (price_list or os.environ.get("ERPNEXT_PRICE_LIST") or COST_PROFIT_PRICE_LIST).strip()
```

- [ ] **Step 4:跑测试确认通过**

Run: `/home/weifashi/hwt/analysis/venv/bin/python -m unittest tests.test_erpnext_api_pricelist -v`
Expected: PASS

- [ ] **Step 5:同步 `.env` 与 `.env.example`**

`.env` 与 `.env.example` 的 `ERPNEXT_PRICE_LIST` 改为 `Buying - Internal`。

- [ ] **Step 6:Commit**

```bash
git add bq_reports/utils/erpnext_api.py tests/test_erpnext_api_pricelist.py .env.example
git commit -m "fix(erp): 价表默认对齐 ttpos Buying-Internal + 禁静默 last_purchase 回退"
```

### Task 1.2:UOM 按 desired 选行(复刻 preferItemUnitCost)

**Files:**
- Modify: `bq_reports/utils/erpnext_api.py:206-238`(分组选行逻辑)
- Test: `tests/test_erpnext_api_pricelist.py`

- [ ] **Step 1:写失败测试**

```python
def test_pick_row_matching_desired_uom(self):
    # 同一物料有 g 与 ctn 两行价,desired='g' 必须取 g 行(对齐 ttpos preferItemUnitCost)
    rows = [{"item_code":"X","uom":"ctn","price_list_rate":300,"modified":"2026-06-02"},
            {"item_code":"X","uom":"g","price_list_rate":0.3,"modified":"2026-06-01"}]
    picked = E._pick_item_price_row(rows, desired_uom="g")
    self.assertEqual(picked["uom"], "g")
    self.assertAlmostEqual(picked["price_list_rate"], 0.3)

def test_no_desired_uom_match_returns_flagged_none(self):
    rows = [{"item_code":"X","uom":"ctn","price_list_rate":300,"modified":"2026-06-02"}]
    self.assertIsNone(E._pick_item_price_row(rows, desired_uom="g"))
```

- [ ] **Step 2:跑测试确认失败**

Run: `/home/weifashi/hwt/analysis/venv/bin/python -m unittest tests.test_erpnext_api_pricelist -v`
Expected: FAIL(`_pick_item_price_row` 未定义)

- [ ] **Step 3:实现**

抽出 `_pick_item_price_row(rows, desired_uom)`:有 desired_uom 时**只接受 uom 精确匹配的行**(多行取 modified 最新);desired_uom 为空时回退现优先级表。`load_erpnext_prices` 接受可选 `desired_uoms: dict[code,uom]`,逐物料用它选行;匹配不到返回 None(交由消费侧判缺口,**不**拿错 UOM 的价充数)。

```python
def _pick_item_price_row(rows, desired_uom=None):
    if desired_uom:
        cands = [r for r in rows if (r.get("uom") or "").strip().lower() == desired_uom.strip().lower()]
        if not cands:
            return None  # 无对应 UOM 价行 = 数据缺口,显式暴露
        return max(cands, key=lambda r: r.get("modified",""))
    # 无 desired:沿用 g>pc>nos 优先级 + modified(兼容旧调用)
    ...
```

- [ ] **Step 4:跑测试确认通过**

Run: `/home/weifashi/hwt/analysis/venv/bin/python -m unittest tests.test_erpnext_api_pricelist -v`
Expected: PASS

- [ ] **Step 5:Commit**

```bash
git add bq_reports/utils/erpnext_api.py tests/test_erpnext_api_pricelist.py
git commit -m "fix(erp): Item Price 按 desired-UOM 选行,无匹配暴露缺口(对齐 preferItemUnitCost)"
```

---

## Phase 2:margin/tax 忠实化(erpnext_price.py)

### Task 2.1:`PRLE-0003` 条件套用,替换无条件硬编码 5%

**Files:**
- Modify: `bom_pipeline/erpnext_price.py:37-92`
- Test: `tests/test_erpnext_price.py`(扩展)

- [ ] **Step 1:写失败测试(测条件逻辑,不依赖具体 rate 值)**

```python
def test_rule_skipped_when_price_list_mismatch(self):
    # for_price_list 限定 Buying-Internal 的规则,查 Standard Buying 时不应套用
    from bom_pipeline.erpnext_price import final_unit_cost_with_rule, PricingRule
    rule = PricingRule(margin_type="Percentage", margin_rate_or_amount=5.0,
                       for_price_list="Buying - Internal", buying=True, disabled=False)
    # 当前价表 = Standard Buying → 规则不适用 → 仅税
    self.assertAlmostEqual(final_unit_cost_with_rule(100.0, tax_rate=7, rule=rule,
                                                     price_list="Standard Buying"), 107.0)
    # 当前价表 = Buying - Internal → 规则适用 → ×1.05 再 ×1.07
    self.assertAlmostEqual(final_unit_cost_with_rule(100.0, tax_rate=7, rule=rule,
                                                     price_list="Buying - Internal"), 100*1.05*1.07)

def test_rule_skipped_when_disabled(self):
    from bom_pipeline.erpnext_price import final_unit_cost_with_rule, PricingRule
    rule = PricingRule("Percentage", 5.0, for_price_list="", buying=True, disabled=True)
    self.assertAlmostEqual(final_unit_cost_with_rule(100.0, 7, rule, "Buying - Internal"), 107.0)
```

- [ ] **Step 2:跑测试确认失败**

Run: `/home/weifashi/hwt/analysis/venv/bin/python -m unittest tests.test_erpnext_price -v`
Expected: FAIL(`final_unit_cost_with_rule`/新字段未定义)

- [ ] **Step 3:实现**

`PricingRule` 加 `for_price_list/buying/disabled` 字段;新增 `rule_applies(rule, price_list)` 复刻 `appliesToItemUnitCost`(buying & !disabled & for_price_list 空或匹配);`final_unit_cost_with_rule(base, tax_rate, rule, price_list)` 仅在 `rule_applies` 时套 margin。保留 `final_unit_cost` 旧入口但标 `DeprecationWarning`,内部走新逻辑(默认规则视为 for_price_list="Buying - Internal")。

```python
def rule_applies(rule, price_list):
    if not rule.buying or rule.disabled:
        return False
    if rule.for_price_list and rule.for_price_list.lower() != (price_list or "").lower():
        return False
    return True

def final_unit_cost_with_rule(base_cost, tax_rate, rule, price_list):
    rules = [PricingRule(rule.margin_type, rule.margin_rate_or_amount)] if rule_applies(rule, price_list) else []
    return calculate_final_item_unit_cost(base_cost, rules, resolve_tax_rate(tax_rate))
```

- [ ] **Step 4:跑测试确认通过**

Run: `/home/weifashi/hwt/analysis/venv/bin/python -m unittest tests.test_erpnext_price -v`
Expected: PASS(含原 v4 基准用例不回归)

- [ ] **Step 5:Commit**

```bash
git add bom_pipeline/erpnext_price.py tests/test_erpnext_price.py
git commit -m "fix(erp): PRLE-0003 按 for_price_list 条件套用,替换无条件 5%(对齐 appliesToItemUnitCost)"
```

---

## Phase 3:消费侧 UOM 不再丢弃(material_price.py)

### Task 3.1:ERPNext 层按 desired-UOM 校验,UOM 不匹配判缺口,退役 `{MK01018:50}` 硬编码

**Files:**
- Modify: `semantic/cogs/material_price.py:22-26, 142-161`
- Test: `tests/test_cogs_layer.py`(扩展)

- [ ] **Step 1:写失败测试**

```python
def test_erp_layer_rejects_uom_mismatch(self):
    from semantic.cogs.material_price import build_material_price_resolver
    # erp_prices 现为 {code:(price,uom)};BOM 该物料 desired='g',ERP 行是 'ctn' → 不得静默用
    erp = {"X": (300.0, "ctn")}
    r = build_material_price_resolver({}, erp, [], desired_uoms={"X": "g"})
    self.assertIsNone(r.resolve(("X", None)))  # UOM 不匹配 → 不命中,交由上层判缺口

def test_erp_layer_accepts_uom_match(self):
    from semantic.cogs.material_price import build_material_price_resolver
    erp = {"X": (0.3, "g")}
    r = build_material_price_resolver({}, erp, [], desired_uoms={"X": "g"})
    res = r.resolve(("X", None))
    self.assertAlmostEqual(res.value, 0.3)
```

- [ ] **Step 2:跑测试确认失败**

Run: `/home/weifashi/hwt/analysis/venv/bin/python -m unittest tests.test_cogs_layer -v`
Expected: FAIL(`build_material_price_resolver` 不接受 `desired_uoms`)

- [ ] **Step 3:实现**

`build_material_price_resolver` 增 `desired_uoms: dict|None`;ERPNext `_fetch_erp` 命中后,若该物料有 desired_uom 且 `_uom` 不匹配 → 返回 None(判缺口);匹配或无 desired 时返回价。删 `BOM_UNIT_CORRECTIONS={"MK01018":50}` 硬编码(改为:正确做法是 ERP 维护 g 级 Item Price,缺则进缺口报警),保留一行注释指向 Phase 4 anchor 验证。

```python
if erp_prices:
    def _fetch_erp(key):
        code, _name = key
        if not code: return None
        for k in (code, str(code).upper(), str(code).lower()):
            if k in erp_prices:
                price, uom = erp_prices[k]
                want = (desired_uoms or {}).get(code) or (desired_uoms or {}).get(k)
                if want and (uom or "").strip().lower() != want.strip().lower():
                    return None   # UOM 不匹配 = 缺口,不充数
                return price
        return None
    providers.append(CallableProvider(name="ERPNext", priority=50, fetch=_fetch_erp))
```

- [ ] **Step 4:跑测试确认通过 + 全量回归**

Run: `/home/weifashi/hwt/analysis/venv/bin/python -m unittest tests.test_cogs_layer -v`
然后 `/home/weifashi/hwt/analysis/venv/bin/python -m pytest tests/ -q`(确认 400+ 不回归)
Expected: PASS

- [ ] **Step 5:Commit**

```bash
git add semantic/cogs/material_price.py tests/test_cogs_layer.py
git commit -m "fix(cogs): ERPNext 层按 desired-UOM 校验,UOM 不匹配判缺口,退役单物料硬编码修正"
```

---

## Phase 4:ttpos 成本对账锚

### Task 4.1:`ttpos_cost_anchor` — 独立读 ERP 按 ttpos 算法复算,逐物料比对管线单价

**Files:**
- Create: `semantic/reconciliation/checks/ttpos_cost_anchor.py`
- Test: `tests/test_ttpos_cost_anchor.py`
- Modify: `semantic/reconciliation/checks/__init__.py`(注册)

- [ ] **Step 1:写失败测试**

```python
# tests/test_ttpos_cost_anchor.py
import unittest
from semantic.reconciliation.checks.ttpos_cost_anchor import compute_ttpos_unit_cost, TtposCostAnchorResult

class TestTtposCostAnchor(unittest.TestCase):
    def test_compute_matches_ttpos_formula(self):
        # 给定 ERP 真值(base, 适用规则, 税),复算应 == ttpos GetItemUnitCost 公式
        cost = compute_ttpos_unit_cost(base=18.0, margin_pct=5.0, applies=True, tax_rate=0.0)
        self.assertAlmostEqual(cost, 18.9)

    def test_anchor_flags_drift(self):
        res = TtposCostAnchorResult(item_code="X", ours=10.0, ttpos=12.0, abs_tol=0.01)
        self.assertTrue(res.is_drift)
```

- [ ] **Step 2:跑测试确认失败**

Run: `/home/weifashi/hwt/analysis/venv/bin/python -m unittest tests.test_ttpos_cost_anchor -v`
Expected: FAIL(模块未建)

- [ ] **Step 3:实现**

`compute_ttpos_unit_cost(base, margin_pct, applies, tax_rate)` = 复用 `erpnext_price` 的纯算法;`TtposCostAnchorResult` 带 `is_drift`(|ours-ttpos|>abs_tol 且相对>rel_tol);`run_anchor(sid, item_codes, our_resolver)` 读 ERP(BI 价 + PRLE-0003 + Item Tax)逐物料复算 ttpos 真值,与 `our_resolver.resolve` 比对,产出 drift 列表 + 覆盖率。文件头注释写明:理想态可替换为调 ttpos `GetItemUnitCost` RPC 当 oracle(见计划方案 B),当前用 ERP 复算(跨 SQL 对账哲学,同 `ttpos_anchor.py`)。

- [ ] **Step 4:跑测试确认通过**

Run: `/home/weifashi/hwt/analysis/venv/bin/python -m unittest tests.test_ttpos_cost_anchor -v`
Expected: PASS

- [ ] **Step 5:Commit**

```bash
git add semantic/reconciliation/checks/ttpos_cost_anchor.py tests/test_ttpos_cost_anchor.py semantic/reconciliation/checks/__init__.py
git commit -m "feat(recon): ttpos 成本对账锚 — 逐物料复算 ERP 真源比对管线单价"
```

### Task 4.2:接锚到成本毛利报表导出(冒烟,非闸门)

**Files:**
- Modify: `bq_reports/pnl_statement.py` 或 `bq_reports/profit_margin_report.py`(成本计算后调 anchor,console 打印 drift Top-N + 覆盖率)
- Test: `tests/test_ttpos_cost_anchor.py`

- [ ] **Step 1–5:** 报表算完 COGS 后,若 `--ttpos-cost-anchor` 开关在,调 `run_anchor` 打印 drift（先观察模式,不 exit;待 Phase 0 量化稳定后再议是否入闸门——参照技术债⑥ CROSS_LEDGER 不进闸门的判断)。TDD 加一个"开关默认关、开则打印"用例,实现,跑通,提交。

### Task 4.3:一次性人工验收 — 对 ttpos 后台成本毛利报表

**唯一能证明"真等于 ttpos"的步骤**(ttpos RPC 够不着,自动锚只能验"ERP 算法一致")。

- [ ] 让有权限同事从 ttpos 后台导出某店某月(非封存月,如 2026-06)的成本毛利/商品成本报表(内部走 `GetItemUnitCost`)。
- [ ] 跑修正后管线同店同月,逐 SKU diff 单位成本。
- [ ] 差 ≤ 抹零级 → 验收过,记进 `docs/audit/<date>-ttpos-cost-parity.md`;系统性差 → 回 Phase 0/1 查哪个输入还没对齐。
- [ ] 不自动化、不入 CI(依赖人工导报表),但**对外宣称"已对齐 ttpos"前必须做一次**。

---

## Phase 5(后续计划,gated on Phase 0 拿到读权限):② clean_bom 重灌

不在本计划展开。摘要:用修正后的 `erpnext_api`(BI + desired-UOM)重拉全量 BOM 物料价,替换 `clean_bom.csv` 里手工迭代的 `基价(原始)/物料单价`;**封存月不动**;与 v40 在"未改口径的物料"上做差异回归;过 `tests/test_erpnext_price` v4 基准复核(注意:v4 基准本身是旧口径,重灌后需更新基准并在 commit 说明口径切换)。另起 `docs/superpowers/plans/<date>-clean-bom-reload.md`。

---

## 开放问题(需 Phase 0 ERP 确认,影响参数非结构)

1. `PRLE-0003` 实际 `margin_rate_or_amount` 与 `for_price_list` —— 决定 5% 是否真在 Buying-Internal 生效。
2. `Buying - Internal` 是否对 wallace 物料按 desired-UOM(尤其 g 级)维护了 Item Price 行 —— 决定 Task 1.2/3.1 会暴露多少"UOM 缺口"。
3. `erp.ttpos.dev` 多租户是否需 site-code 头 —— 决定 Phase 0 探针能否直连(Go `WithSiteCode`)。
4. Item Tax 模板的 min/max net band 在 wallace 是否启用 —— 决定 Phase 2 税侧是否需补 band 逻辑(当前忠实复刻已含,值待验)。

## Self-Review 备忘

- Spec 覆盖:5 维偏差(价表/字段/UOM/margin/税)→ Task 1.1(价表+字段)、1.2+3.1(UOM)、2.1(margin)、Phase 2 税侧 + 开放问题 4(税 band)、Phase 4(锚总验)。✅
- 类型一致:`erp_prices` 全程 `{code:(price,uom)}`;`desired_uoms` 全程 `dict[code,uom]`;`PricingRule` 新字段在 2.1 定义、4.1 复用。✅
- 无占位:ERP 依赖参数集中在 Phase 0 fixture + 开放问题,代码任务测的是**条件逻辑**(可在无 ERP 下确定),非具体数值。
