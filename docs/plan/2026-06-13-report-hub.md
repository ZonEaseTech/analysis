# Plan: Wallace Report Hub (internal data platform)

- Status: M1-M3 integrated & verified on :36722 (incl. SSE). Fixes 2026-06-15: (1) spawnPython PYTHONPATH=repo root → bq_reports absolute imports work; (2) data-dict audit (hub/scripts/audit_datadict.py) found & fixed 2 field errors + 3 unsynced member tables in bq-schema-reference.md. (3) script CLI args fixed: run endpoint auto-injects defaults (--output → timestamped exports/ path, --month → previous month) by scanning source; verified bom_export runs end-to-end via web (connects BQ, streams logs). All-optional scripts run as-is. OPEN: commit pending; advanced per-script param form (option A) deferred.
- Owner: weifashi (with Claude)
- Created: 2026-06-13
- Task: docs/task/index.md → report-hub

## Goal

A locally-deployed internal data platform that ends the current mess: 84 report
scripts scattered across 3 dirs, 300 xlsx in exports/, field docs scattered, and
no way to verify report numbers. Single entry point on `0.0.0.0:36722` (coder
forwards the port).

## Tech stack (confirmed installable on this box)

- Frontend: pma-web — React 19 + TS + Vite 8 + TanStack Router + shadcn/ui + Tailwind v4 (node v22 ✓)
- Backend: pma-bun — Bun 1.3 + Hono + Bun built-in SQLite (bun 1.3.14 installable ✓)
- Data execution: keep Python (venv/bin/python); backend spawns it, does NOT rewrite report logic
- Metadata store: Bun SQLite (script registry, field docs, run records)
- Deploy: Bun serves built frontend + API on 0.0.0.0:36722

## Layout

```
hub/
  api/        # Bun + Hono backend
  web/        # React + Vite frontend
```
No existing report script is modified — the hub only reads/schedules them.

## Milestones (deliver all in one pass: M1→M2→M3)

### M1 — skeleton + script center + report preview
- Bun+Hono server on 36722, serves built React app + /api
- SQLite schema + script scanner: parse `# 谁问的/问什么` headers across bq_reports/, scripts/, scripts/adhoc/
- Script list page: what report / who asked / last run
- Run a script (spawn venv/bin/python) with live log stream (SSE)
- Report center: group exports/ xlsx by name/month/version; in-browser table preview (openpyxl→JSON) + download

### M2 — data dictionary
- Import docs/bq-schema-reference.md + metrics-catalog.md + semantic/ into structured tables/fields/metrics
- Browse + search; table relationship view

### M3 — audit center
- Surface per-report audit artifacts (e.g. exports/audit_2026-05/ five files)
- Step-by-step verifiable calculation chain UI ("does the money tie out")

## Risks / mitigations
- Self-built tools rot after author leaves → mainstream stack + docs
- Long-running scripts / concurrency → job queue + SSE
- Security (runs scripts, 0.0.0.0) → rely on coder auth; run-type ops need confirm; default read-only
- Large xlsx preview → row limit / pagination
- Scope is large → strict module boundaries, shared API contract defined first

## Verification
- `bun run` server boots, 36722 reachable
- script scan lists ≥44 scripts with metadata
- run a known script (e.g. report) streams logs and produces output
- preview a known xlsx renders rows
- frontend build served by bun (prod), vite proxy in dev
