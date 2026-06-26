# Plan: hub/web pma-web compliance — close the Required gates

- Status: **completed** (2026-06-16)
  - ESLint @antfu (semi/double preserved; JSX-restructuring fixers disabled to
    protect significant whitespace; route-export rule off for TanStack routes).
    `bun run lint` green (0 errors, 14 advisory warnings on existing code).
  - Vitest 4 + jsdom + @testing-library/react; 5 focused tests (http + Badge).
  - Backend `test` script scoped to `src/` so `bun test` stops recursing into
    web/ (was running the vitest files under bun:test and failing).
  - CI hub-web job runs lint + test + build.
  - UI-library migration to shadcn/Base UI deferred (recorded deviation).
- Owner: weifashi (with Claude)
- Task: docs/task/index.md → hub-web-compliance
- Stack: /pma-web

## Context / findings (audited against pma-web baseline + review)

hub/web vs the pma-web Required stack:
- React 19 ✓ · TS 5.9 strict ✓ · Vite 8 ✓ · Tailwind v4 ✓ · TanStack Query 5 ✓
  · TanStack Router ✓ · `shared/lib/http.ts` ✓ · feature `useXxxQuery` ✓
- 🔴 **Vitest 4** — missing (no test runner, no tests, no `test` script).
- 🔴 **ESLint + @antfu/eslint-config** — missing on web (backend has it).

## Deviation surfaced (NOT silently fixed)

⚠️ **UI Library Policy**: pma-web mandates shadcn/ui (base-nova) + `@base-ui/react`
as the *only* allowed UI ecosystem. hub/web's primitives under
`src/shared/components/ui/` (badge, button, card, drawer, input, table, tabs)
are **hand-written**, with no `components.json` and no `@base-ui/react`. This is
a hard-constraint deviation.

It is deliberately **out of scope** for this closure: migrating 7 primitives +
every consumer to shadcn/Base UI is a large, high-blast-radius change that does
not belong bundled into "add the missing test/lint gates". Recorded as a
separate follow-up decision (see Alternatives). Closing the test/lint gates does
not depend on it.

## Decisions (explicit per pma-web)

- ESLint: adopt `@antfu/eslint-config`, but configure `stylistic` to **preserve
  the frontend's existing semicolons + double-quotes** (Surgical Changes — don't
  mass-reformat ~25 files that aren't broken). The rule engine (the Required
  gate) is satisfied; logic/quality rules are enforced; formatting is unchanged.
  Backend keeps antfu defaults (no-semi) — separate sub-project, separate config.
- Vitest 4 with jsdom + @testing-library/react for component tests. Focused tests
  around changed behavior (http layer + a render smoke), not snapshot churn.
- Keep the fixed-port (36722, coder-forwarded) deviation from nsl — already
  documented in hub/README; not reopened here.

## Goal / success criteria

1. `bun run lint` (web) green -> verify: eslint exits 0.
2. `bun run test` (web) green -> verify: vitest run passes, ≥1 pure + ≥1 component test.
3. CI hub-web job runs lint + test + build -> verify: ci.yml updated, commands proven locally.

## Scope
- IN: web eslint config + lint script; vitest config + setup + deps + tests +
  test script; CI hub-web job gains lint + test; accessibility note for touched UI.
- OUT: UI-library migration to shadcn/Base UI (separate task); nsl dev routing;
  Zustand (no shared UI state needs it); i18n (single locale).

## Alternatives (for the deviation)
- **A1 Migrate UI to shadcn/Base UI now** — full compliance, but large blast
  radius; rejected for this slice, recommended as its own task.
- **A2 Keep hand-written primitives, document the deviation** — chosen for now;
  the primitives consume the same Tailwind tokens and a11y patterns, so the gap
  is policy-conformance, not user-facing breakage.
