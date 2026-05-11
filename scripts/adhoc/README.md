# scripts/adhoc/

One-off investigation / debug scripts. **Kept for audit trail only.**

Distinguishing rule vs the rest of `scripts/`:

> If a script is part of regular workflow (reconciliation, payment ingest,
> validation that runs each month) → it lives one level up, in `scripts/`.
> If it was written to answer a specific question once and then dropped
> from rotation → it belongs here.

Anything in this directory:

- May reference paths / data files that no longer exist.
- May have stale `sys.path` injections (e.g. `discover_schema.py` still points
  at someone's old macOS workstation).
- Is **not** covered by `tests/` and never will be.
- Is **not** safe to assume it works as a regular tool. Read it first.

If you find yourself running one of these regularly, that's a signal it
should graduate out of `adhoc/` — either by moving up to `scripts/` (after
cleaning it up) or by absorbing its function into a proper report under
`bq_reports/`.

## Current inventory

| File | Purpose |
| --- | --- |
| `compare_outputs.py` | Diff two profit-margin Excel exports cell-by-cell. Used during a v23 → v24 cutover. |
| `discover_schema.py` | One-off BQ probe to confirm combo-structure storage. |
| `validate_report.py` / `validate_v2.py` | Profit-margin cross-validation against an older 15-column format. Superseded by `scripts/validate_report_semantics.py`. |
| `debug_erpnext_auth.py` | Sanity-check ERPNext SID / API key auth (was `test_erpnext_auth.py` — renamed so `unittest discover` doesn't accidentally pick it up). |
| `debug_erpnext_uom.py` | Probe ERPNext UOM conversion table (was `test_uom.py` — same renaming reason). |

## Migration candidates still in `scripts/`

These also fit the "adhoc" definition but were not moved in the current
cleanup pass. Migrate when you next touch them:

- `drop_listprice_receivable.py` — v38 → v39 column-removal patch. One-shot.
- `calc_sales_price.sql` / `verify_sales_price.sql` / `sales_price_sql.py`
  / `investigate_price_change.sql` — sales-price investigation SQL.
- `verify_deleted_combo.py` / `verify_deleted_single.py` — BOM-deletion checks.
- `list_april_materials.py` / `parse_merged_bom.py` — one-off data dumps.
