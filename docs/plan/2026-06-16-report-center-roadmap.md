# Roadmap: make the hub fit-for-use as a 报表中心 (items 1→4)

- Status: **proposed — awaiting approval** (do not implement before `proceed`)
- Owner: weifashi (with Claude)
- Created: 2026-06-16
- Stack: /pma-bun (backend) + /pma-web (frontend); Python for capture hooks
- **Confirmed sequence (2026-06-16): 1 → 2 → 4** (the light core = catalog + run
  + status/对账 + deliver + freshness). **Item 3 (declarative derivation) is
  PARKED** — it re-architects how reports are *made*, not how they're *managed*;
  YAGNI, revisit only when copy-paste pain is real and recurring (CI makes it
  cheap to add later). Auth/RBAC deferred until its trigger (external users /
  untrusted run access / download audit need).
- **Held, not scheduled**: trimming the user-facing nav to catalog/status/deliver
  (operator-only pages tucked away). User wants to think it over first.
- North star (the ruler): the hub *manages existing reports*, it does not reinvent
  how reports are built. Every new feature must pass that test.

This roadmap sequences four initiatives. Each becomes its own task + Phase-1
proposal when reached; this file is the order, dependencies, and rough scope.
Near items (1–2) are specified concretely; later items (3–4) are lower-resolution
and refined when reached.

## Item 1 — surface 对账 (validator) results in the hub  ·  ~1 day  ·  task: validator-capture

**Why first**: highest ROI, data already produced. Every report script already
runs `semantic/validators` and prints ✅/🟡/🔴 (`profit_by_price_report.py:1034`
`print_result(...)` + `result.has_must_fix`). The hub captures none of it
(`grep validator hub/src` = empty). Making it visible turns the hub from a file
window into a *trustworthy* report center.

**Approach** (least-invasive, robust):
- Extend `semantic/validators/core.py`: add `Result.summary()` → dict
  `{must_fix, needs_review, ok, total}` and an optional `json_path` param on
  `print_result(...)` that writes a `<output>.validation.json` sidecar.
  Backward compatible (one optional arg).
- The few report scripts the hub runs pass `json_path` next to their `--output`.
- Backend: on run completion, read the sidecar (if present) and store a
  `validation` summary on the run row (new nullable columns via drizzle-kit).
- Frontend: a ✅/🟡/🔴 badge on the 运行历史 rows and the 报表中心 cards;
  a "对账" panel in the run drawer.

**Depends on**: run history (done). **Risk**: sidecar path coupling — mitigate by
deriving it from `--output`. **Verify**: run a report → run row shows the
validation badge; a 🔴 run is visibly flagged.

## Item 2 — self-serve parameterized runs  ·  ~1.5 day  ·  task: self-serve-params

**Why**: today a run uses hardcoded defaults (`scripts/index.ts defaultArgs`:
month = last month, output = timestamp). A non-technical user cannot get "March,
store X". This decides *who besides the operator can use it*.

**Approach** (curated whitelist, NOT raw argparse exposure):
- Declare a `params:` block per report in `resources/reports/*.yaml` (or a hub
  config): only user-facing params, each `{name, label, type, choices?,
  default, required}`. argparse has many internal/dangerous flags
  (`profit_by_price_report.py:767+`) that must NOT be exposed — whitelist only,
  mirroring the resolvers `allowed_categories` safety pattern.
- Backend: `GET /api/scripts/:id/params` returns the schema; `POST /run` accepts
  a validated param map (Zod at the boundary) and builds argv from the whitelist.
- Frontend: a small form (month picker, store/mode select) before run.

**Depends on**: nothing hard. **Risk**: param injection — mitigate by building
argv only from whitelisted keys with typed values, never passing raw strings.
**Verify**: pick a month in the UI → script runs with that `--month`; an
unlisted/garbage param is rejected.

## Item 3 — declarative report derivation  ·  PARKED (YAGNI)  ·  task: sustainable-derivation (existing plan)

> Parked 2026-06-16: out of the "manage existing reports" core. Revisit only when
> copy-paste-a-new-report pain is real and recurring. The spike below is its
> entry gate when/if it is unparked.


**Why**: "new report = copy 200 lines" (`CLAUDE.md:99`) caps how fast the report
catalog can grow. Already designed in
`docs/plan/2026-06-15-sustainable-report-derivation.md` (Phase 1).

**Gate (unverified 命门)**: before committing, a spike must prove a declarative
spec can express ≥1 real existing report shape (top-5 price columns, merged BOM
blocks). If the spike fails, re-scope. Do NOT migrate the 1600-line scripts.

**Depends on**: best after item 2 (a derived report and a parameterized run both
touch "how a report is defined/invoked" — align the param schema with the spec).
**Verify**: one new report produced purely from a spec; contract test green.

## Item 4 — output-side report catalog + freshness  ·  ~1.5 day  ·  task: report-catalog

**Why**: `reports/index.ts` groups 306 loose xlsx by filename heuristic. Users
should see *report types* (利润表/成本表/…), each with its latest version,
freshness ("data through YYYY-MM"), producing script/run, and (from item 1) its
validation status.

**Approach**: link an output xlsx → its run record (the run already knows
`--output`; persist the produced path on the run row) → script + params + the
validation summary. Group reports by type; show freshness from the run's
`finishedAt` + the `--month` covered.

**Depends on**: items 1 (validation status) + the run history (done). **Verify**:
the 报表中心 shows report types with freshness + validation, and an xlsx links
back to the run that made it.

## Cross-cutting
- Every item ships its gates green (the CI floor from `hub-memory`): metrics
  checks + hub typecheck/lint/test + web lint/test/build.
- Each item is independently shippable and reversible; stop/reassess between.
- Auth/RBAC stays out until its trigger (see header).
