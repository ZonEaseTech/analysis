# Plan: Metrics Registry — single source of truth for 口径 / formulas, rendered by hub

- Status: **completed (A+B+C)** (2026-06-15)
  - Phase A `9e31447`: registry (26 metrics / 5 domains) + schema + loader +
    render_catalog generator (`--check` CI gate) + 15 tests. metrics-catalog.md
    is now generated.
  - Phase B `957d224`: `/api/metrics` + 口径中心 page (domain cards, search,
    detail drawer with formula/SQL/lineage/anchor/confidence). Also recovered
    hub/web/src/shared/lib/ which the root `lib/` gitignore had been dropping.
  - Phase C `0113c91`: report yaml `metric:` bindings (profit_margin.yaml ×9) +
    contract gate (validate_bindings) + ColumnConfig.metric field (additive) +
    `/api/metrics/bindings` + 报表血缘 page. 18 tests green.
- Owner: weifashi (with Claude)
- Created: 2026-06-15
- Task: docs/task/index.md → metrics-registry
- Related: docs/plan/2026-06-13-report-hub.md (the hub this renders into),
  docs/architecture-evolution-roadmap.md (P1–P5 = compute infra; this is a
  parallel *metadata* layer, not part of P1–P5)

## Context / findings (Phase 1 investigation)

The workspace already contains ~80% of a semantic / metrics layer, but each
metric definition is **fragmented across three incompatible representations**,
with no machine-readable single source of truth:

1. **Prose** — `docs/metrics-catalog.md`: 21 metrics, each with 业务含义 /
   公式 / SQL impl (file:line) / source tables / reconciliation anchor /
   report display. Hand-maintained; the doc itself admits drift risk
   ("改口径=改本文档", enforced only by human discipline).
2. **Python** — `semantic/` (4407 lines): entities (SQL CTE = anti-corruption
   layer), aggregations (`pnl_layers`, `kpi_ratios`), resolvers, validators
   (`identities.py` = accounting identities), reconciliation (anchor checks).
   The actual compute. Reports import it (`profit_by_price`, `pnl_statement`,
   `profit_margin`, … all `from semantic ...`).
3. **YAML** — `resources/reports/*.yaml`: column definitions where formulas
   are re-encoded a third time as Excel strings (`=SUMPRODUCT(...)`) plus
   per-column 口径 comments.

The hub (`hub/`) surfaces #1 by **regex-parsing the markdown prose**
(`hub/src/modules/datadict/index.ts`) — fragile, lossy, no lineage.

`semantic/metrics/__init__.py` is an **empty stub** whose docstring already
states the intended fix: *"populated as reports migrate from inline yaml
comments to metric-key references."* This plan builds that stub.

Community standards confirm the direction (metrics-as-code / headless BI):
- **dbt Semantic Layer / MetricFlow** — metrics defined once in YAML
  (entities / measures / dimensions / metrics), SQL built at query time;
  converging on the Open Semantic Interchange (OSI) interchange format.
- **Cube** — open-source *headless* semantic layer: define metrics once in
  code, expose downstream; "Cube Core ships no UI, you build your own" =
  exactly the hub's role.
- **Open Data Contract Standard (ODCS) v3.1.0** (Bitol/LF) — data contracts
  with a structured quality-check block (operators, valid ranges) = exactly
  what `validators/identities.py` already does, just not declared as a contract.

Decision: **do not adopt dbt/Cube wholesale** (would re-platform the Python
compute we deliberately keep, and our compute is BQ + Excel-formula +
multi-source resolver + reconciliation, not a clean warehouse star schema).
Instead build a **lightweight in-house metric registry** that *borrows their
vocabulary*, becomes the single source of truth, and is rendered by the hub.

## Goal

One machine-readable metric registry as the single source of truth for every
口径. Everything downstream renders FROM it:
- `metrics-catalog.md` is **generated** from the registry (drift dies).
- the hub renders a real **口径中心 / Metric Catalog** page from structured
  data (kills regex markdown parsing).
- report YAML columns and `validators/identities` **reference metric IDs**.
- per-metric **lineage** (report → metric → SQL entity file:line → source
  table/field → reconciliation anchor → confidence) is browsable.

Maps to the 5 asks: 可追溯取值口径 (lineage+anchor+confidence) ·
可复用计算逻辑 (define-once, reference-by-N) · 按业务场景模块化 (domain
grouping) · 计算公式可提取 (formula is a structured field) · 方便人类阅读
(hub catalog page).

## Proposal — 3 phases (each independently shippable)

### Phase A — registry schema + author + generator (~1 day)
- Define a minimal metric-spec schema (in-house YAML, MetricFlow/Cube/ODCS
  vocabulary): `id, name, domain, definition, formula{business, sql_ref,
  excel}, grain, lineage{source_tables, source_fields, entity_file_line},
  reconciliation_anchor, confidence(ACTUAL|ESTIMATED|N/A), status`.
- Author `semantic/metrics/registry/*.yaml` (one file per domain: sales /
  settlement / finance / kpi / metadata) from the existing 21 catalog metrics.
- Generator `semantic/metrics/render_catalog.py` → regenerates
  `docs/metrics-catalog.md`. Verify: generated ≈ current (diff reviewed once).
- Loader `semantic/metrics/loader.py` + schema validation test.

### Phase B — hub 口径中心 page (~1 day)
- New backend module `hub/src/modules/metrics/` reads the structured registry
  (replaces the regex markdown parse in `datadict`).
- New frontend route `/metrics`: domain-grouped metric cards (含义 / 公式 /
  SQL ref / 来源 / 对账锚 / confidence badge), full-text search, file:line
  deep-links. Reuses existing shadcn UI + TanStack patterns.

### Phase C — report ↔ metric binding + lineage (~1.5 day)
- Add optional `metric: <id>` to `resources/reports/*.yaml` columns; engine
  pulls the 口径 comment from the registry (the stub's stated plan).
- CI/test gate: every referenced `metric` id must exist in the registry.
- hub renders report → metric → source lineage: "for this report, every
  column, where each number comes from."

## Risks / mitigations
- **Drift just moves up a level** (registry authored once, forgotten) →
  generator + a CI check that `metrics-catalog.md` is regenerated; mechanize
  the existing "改口径=改文档" PR rule instead of relying on discipline.
- **Over-engineering 21 metrics** → in-house YAML, zero new runtime deps,
  no query engine; spec stays ~10 fields.
- **Scope creep into P1–P5 compute** → this touches metadata only; entities /
  resolvers / aggregations are NOT modified.
- **Hub not yet committed** → land `.gitignore` + commit the hub first so this
  work has a clean base.

## Scope (in / out)
- IN: `semantic/metrics/` (registry + loader + generator), regenerated
  `docs/metrics-catalog.md`, hub `metrics` module + page, optional
  report-yaml `metric:` binding.
- OUT: any change to entities / resolvers / aggregations / reconciliation
  compute; adopting dbt/Cube; multi-tenant; auth.

## Alternatives
- **A1 Adopt dbt Semantic Layer / Cube** — standards-native, but re-platforms
  Python compute and assumes a warehouse star schema we don't have. Rejected;
  instead borrow vocabulary so the in-house spec can export to OSI later.
- **A2 Keep prose-only, just improve the hub markdown parser** — cheapest, but
  leaves 3 representations and the drift problem unsolved. Rejected.
- **A3 Phase A only** (registry + generated docs, no hub page) — valid minimal
  cut if scope/time is tight; delivers the SoT without the UI.

## Verification
- Phase A: `render_catalog.py` output diff-reviewed vs current catalog; loader
  schema test passes; all 21 metrics present.
- Phase B: `/metrics` renders all domains; search works; deep-links resolve.
- Phase C: CI rejects an unknown `metric:` id; lineage view renders for one
  report end-to-end.
