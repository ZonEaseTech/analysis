# Wallace Report Hub

Internal data platform for the Wallace (Thailand) BigQuery report tooling: one
place to see every report script, run it with live logs, browse generated xlsx
reports, read the data dictionary, and verify report numbers via audit chains.

Single entry point on `0.0.0.0:36722` (coder forwards this port).

## Stack

- Backend: Bun + Hono (`hub/src/`), SSE for live logs, spawns `venv/bin/python` to run reports
- Frontend: React 19 + Vite 8 + TanStack Router/Query + shadcn-style UI (`hub/web/`)
- Data execution stays in Python — the hub only schedules/reads it, never re-implements report logic

## Run

```bash
export PATH="$HOME/.npm-global/bin:$PATH"

# --- production (single entry on :36722, serves built frontend + API) ---
cd hub/web && bun run build          # build frontend → hub/web/dist
cd ../  && bun src/index.ts          # serve on 0.0.0.0:36722

# --- development (two processes) ---
cd hub      && bun --watch src/index.ts   # API on :36722
cd hub/web  && bun run dev                 # Vite on :5173, proxies /api → :36722
```

Then open the coder-forwarded `:36722`.

## Modules / API

- `GET /api/scripts`, `/api/scripts/:id`, `POST /api/scripts/:id/run`, `GET /api/runs/:id/stream` (SSE)
- `GET /api/reports`, `/api/reports/preview`, `/api/reports/download`
- `GET /api/datadict/tables`, `/api/datadict/metrics`
- `GET /api/audit/runs`, `/api/audit/file`

## Notes / deviations

- Port fixed to 36722 for both dev and prod (coder forwards it); nsl dev routing intentionally skipped.
- Frontend is a single sibling app (`hub/web/`), not a Bun monorepo.
- Data dictionary is parsed live from `docs/bq-schema-reference.md` + `docs/metrics-catalog.md`.
- Report grouping is by normalized filename; can be refined later.
