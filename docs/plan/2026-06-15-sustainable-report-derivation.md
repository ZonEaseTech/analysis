# Plan: Sustainable report derivation — composition with blast-radius isolation

- Status: **proposed — awaiting approval** (do not implement before `proceed`)
- Owner: weifashi (with Claude)
- Created: 2026-06-15
- Task: docs/task/index.md → sustainable-derivation
- Related: docs/plan/2026-06-15-metrics-registry.md (the registry this builds on),
  docs/architecture-evolution-roadmap.md

## Context / findings (Phase 1, adversarial — every fact self-verifiable)

User's three claims, scored against the code + community lessons:

**Claim 1 — "每个报表派生出一个新统计维度"** → ✅ right, partly already real.
- 🔴 `semantic/aggregations/by_grain.py:3` docstring: *"writing a new report =
  picking a grain. SQL doesn't change."* The clean derivation seam EXISTS.
- ⚠️ Caveat: free ONLY when the dimension column already lives in `sale_event`.
  A genuinely new raw dimension is an upstream (staging) change with its own
  blast radius, not a free mart derivation.

**Claim 2 — "基于已固化报表稳固推进"** → 🟡 happening, but in the dangerous form.
- 🔴 `bq_reports/combo_bom_detail_report.py:30` imports ~10 PRIVATE functions
  (`_bom_for_item`, `_build_sql_templates`, `_load_boms`, `_match_bom_layered`…)
  from `profit_by_price_report`, which imports from `profit_margin_report`.
  DAG: profit_margin(1630L) ← profit_by_price(1080L) ← combo_bom_detail(523L).
- 🔴 `CLAUDE.md:99`: new report = "新 yaml + **复制 200 行报表脚本**".
- So "已固化" is a misnomer: the base is a live 1600-line script downstream
  reaches INTO — deep-import coupling + copy-paste, the exact anti-pattern the
  dbt/semantic-layer community warns about.

**Claim 3 — "即便错，也是错派生的那部分" (blast-radius isolation)** → ⚠️ TRUE one
direction, FALSE the other. This is the crux; the two are likely conflated.
- ADDITIVE (make a NEW derived report, derivation logic wrong): ✅ TRUE —
  adding combo_bom_detail can't change profit_by_price (no reverse import).
  Isolation holds for new leaves.
- MUTATION (EDIT the base "已固化" report): ❌ FALSE — an error in
  profit_margin/profit_by_price propagates DOWN through the private imports to
  combo_bom_detail. Derivation here AMPLIFIES blast radius on base edits.
- 🔴 Community separates the two mechanisms: derivation = reuse; isolation =
  CONTRACTS + model versions + deprecation windows + tests at boundaries
  (dbt contracts "catch unintended changes to column names/types that break
  downstream"; model versions give a "migration window").

**Sustainable-delivery machinery gap (pma-web, /pma-web prefix)** → 🔴
- hub has 0 frontend tests, 0 backend tests, no eslint installed, no vitest,
  no `.github/workflows`. pma-web mandates Vitest 4 + eslint + CI gates. So
  "可持续交付" is currently aspirational — nothing mechanically guards a change.

## The adversarial thesis

You're asking *derivation* to do two jobs — reuse AND error-isolation. The
community uses TWO mechanisms on purpose. Derivation alone makes blast radius
WORSE on the dangerous (base-edit) path. To actually get "错只在派生那部分" you
need: the base FROZEN behind a contract + boundary tests, and derivation
expressed as DECLARATIVE config referencing governed metrics — not 200-line
copies, not private-function imports.

## Proposal — layer the report tier, make derivation declarative (phased)

Leverages what we just shipped (the metric registry = the governed contract).

### Phase 1 — proof of the seam (small, low blast radius)
- Extract ONE shared report builder: `bq_reports/derived/report_spec.py` — reads
  a declarative report spec (base entity + grain + registry metric ids + columns)
  and emits the xlsx via the existing `report_engine`. No rewrite of the 3 big
  scripts; they stay.
- Author ONE new report purely declaratively (e.g. an existing need) to prove
  "new report = config, not 200 lines". If the spec is wrong, only the spec is
  wrong.
- CI/test gate: a `.github/workflows/ci.yml` that runs `render_catalog --check`,
  the binding contract test, and the report identity validators. This is the
  mechanical floor for "可持续交付".

### Phase 2 — freeze the base behind a contract
- Define a "report contract": output column shape + which identities must hold,
  stored next to the report spec. Editing a base report runs the contract test;
  the hub shows blast radius (which specs derive from it — extends 报表血缘).
- Migrate the deep private-import seam (combo→by_price) to reference shared
  builders / registry metrics instead of `_private` functions, one at a time,
  byte-diff verified (the project's existing diff-excel discipline).

### Phase 3 — hub as the derivation cockpit (pma-web)
- "Derive a report" UI: pick base + add dimension + pick registry metrics → new
  spec. Blast-radius view before any base edit. hub gains Vitest + eslint per
  pma-web; the 报表血缘 page becomes the impact-analysis surface.

## Risks / adversarial guardrails
- **Over-engineering ~20 reports** (Simplicity First): keep the spec ~10 fields,
  ONE builder, no framework. Phase 1 is one builder + one report + one CI file.
- **The fix must not create the blast radius it cures**: do NOT rewrite the
  1600-line scripts up front. New reports use the declarative path; old scripts
  migrate only when byte-diff proves equivalence. Eat our own dog food.
- **Contract theater**: a contract with no test is decoration — every contract
  ships with a failing-first boundary test.
- ⚪ Unverified: that a declarative spec can express ALL current report shapes
  (price-breakdown top-5, merged BOM blocks). Phase 1 validates this on ONE
  report before committing to migration.

## Scope
- IN: shared declarative report builder, one proof report, CI gate, report
  contract concept, hub derivation/blast-radius UI + frontend test/lint setup.
- OUT: rewriting profit_margin/by_price/pnl_statement; changing any number
  these reports currently produce; adopting dbt/Cube as a runtime.

## Alternatives
- **A1 Adopt dbt** (its layering + contracts + versioning are exactly this) —
  but re-platforms the Python+BQ+Excel compute; rejected as runtime, mined for
  patterns. Borrow staging→intermediate→marts + contracts + versions.
- **A2 Do nothing, keep copy-200-lines** — cheapest; the coupling + blast-radius
  debt compounds with every new report. Rejected.
- **A3 Phase 1 only** (builder + one report + CI) — valid minimal cut; proves
  the seam and installs the delivery floor without touching old scripts.
