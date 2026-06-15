# Plan: hub memory + delivery floor (方向1 — SQLite run history + CI gate)

- Status: **approved (开始执行) — implementing** (2026-06-15)
- Owner: weifashi (with Claude)
- Task: docs/task/index.md → hub-memory
- Stack: /pma-bun (backend) + /pma-web (frontend)
- Related: docs/plan/2026-06-13-report-hub.md (original plan named "Bun SQLite"
  metadata store — never built), docs/plan/2026-06-15-metrics-registry.md

## Context / findings (all self-verifiable)

- 🔴 Runs are in-memory only: `hub/src/modules/scripts/index.ts:16`
  `const runs = new Map`. Restart loses all history. The original plan
  (`2026-06-13-report-hub.md`) named "Metadata store: Bun SQLite" — not built.
- 🔴 No CI: no `.github/workflows`. No mechanical guard on any change.
- 🔴 hub has no tests; backend `lint` script exists but eslint isn't installed.

This is 方向1 from the assessment: give the hub *memory* (persisted runs) and a
*delivery floor* (CI gate). Foundation for 方向2 (validator capture) and 方向3
(declarative report derivation).

## Decisions (deviations explicit per pma-bun)

- Storage: Drizzle ORM + libSQL (SQLite), per pma-bun default. Schema in
  `hub/src/db/schema.ts`, bootstrap in `hub/src/db/index.ts`, generated
  migrations under `hub/drizzle/` via `drizzle-kit generate` (PMA rule 10 — no
  hand-written migrations). DB file gitignored.
- Keep plain Hono (not OpenAPIHono) — sanctioned Alternative in pma-bun; do NOT
  re-platform the existing server. Surgical: add a db layer + a repository,
  rewrite only the scripts run-state to persist.
- Frontend Vitest (pma-web required gate) is NOT in this slice — flagged as a
  known remaining gate to avoid ballooning. This slice ships backend tests
  (bun:test) + CI.

## Goal / success criteria

1. Runs persist across restart -> verify: start a run, restart hub, run still
   listed with its logs.
2. `GET /api/runs` returns recent run history; script list `lastRunAt` comes
   from DB -> verify: endpoint returns rows, frontend 运行历史 page renders.
3. CI gate green -> verify: `.github/workflows/ci.yml` runs python metric tests
   + `render_catalog --check` + binding contract + hub typecheck/lint/build/test.

## Scope

- IN: `hub/src/db/` (Drizzle schema + bootstrap + migration), runs repository,
  scripts module persists runs, `GET /api/runs` + `GET /api/runs/:id`, frontend
  /runs 运行历史 page + nav, eslint @antfu config for backend, backend bun:test
  for the runs repo, `.github/workflows/ci.yml`, `.gitignore` for the db file.
- OUT: validator-result capture (方向2), scheduling, declarative report specs
  (方向3), frontend Vitest harness, auth.

## Steps -> verify

1. Add Drizzle/libSQL/drizzle-kit deps (latest stable) -> verify: bun install ok.
2. schema.ts (runs) + db/index.ts (WAL, migrate on boot) + drizzle.config +
   generate migration -> verify: db file created, table exists.
3. runs repository + rewrite scripts module to persist -> verify: bun:test green.
4. /api/runs endpoints + script lastRunAt from DB -> verify: curl returns rows.
5. frontend /runs page + nav -> verify: build, HTTP 200, renders history.
6. eslint @antfu config -> verify: bun run lint passes.
7. CI workflow -> verify: yaml valid, jobs cover python + hub gates.

## Risks
- **Drizzle for one table = over-engineering?** Mitigated: the memory layer is
  multi-table by design (runs now; validator results / specs / schedules next).
- **Persisting logs bloats DB**: store run log as a text column, cap size; live
  streaming stays in-memory during the run, full log flushed to DB on completion.
- **CI python deps**: metric tests need only pyyaml (no BQ) — CI installs pyyaml,
  not the full BQ stack.
