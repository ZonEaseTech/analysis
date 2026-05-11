# tests/

Regression net for the upcoming semantic-layer refactor. Pins down **business
semantics** (SQL clauses, price chain, fuzzy-match cascade, column index
contract) so the move from `bq_reports/profit_margin_report.py` to
`semantic/entities/*` + `reports/profit_margin/*` can't silently change a number.

## Run

```bash
venv/bin/python -m unittest discover tests          # all
venv/bin/python -m unittest tests.test_sql_snapshots -v   # one module
```

No `pip install` required — stdlib `unittest` only.

## Layout

| File | Pins down |
| --- | --- |
| `_setup.py` | sys.path bootstrap + `order_row()` synthetic row factory |
| `test_sql_snapshots.py` | `_PROFIT_SALES_TPL` / `BOM_SQL` / `COMBO_STRUCTURE_SQL` business clauses (time windows, state=60 handling, FULL OUTER JOIN, soft-delete fallback) |
| `test_price_resolver.py` | `_resolve_base_unit_price` priority chain (uploaded > ERPNext > BQ) and `BOM_UNIT_CORRECTIONS` divisor |
| `test_fallback_bom_match.py` | `_match_fallback_bom` 5-level cascade + the "鸡肉芝士球 vs 2 盒装" regression; `_apply_bom_overrides` for `BOM_REPLACEMENTS`/`BOM_DROP_CODES` |
| `test_aggregate_with_bom.py` | combo rollup, single-mode dedup, weighted member-discount, BOM tuple shape |
| `test_build_rows.py` | column index contract (34 columns) cross-checked against `profit_margin.yaml` field_index |
| `test_uploaded_prices.py` | `_load_uploaded_prices` Excel parsing (干冻货 / 设备材料 / 盘点单位匹配分析) |
| `test_engine_writer.py` | `write_configured_sheet` round-trip: header comments, block merges, block_formula placement, negative_red font, positive_red conditional format, freeze panes |
| `test_time_window.py` | `_month_to_ts_range` BKK +07:00 month boundaries (incl. Dec roll-over, leap & century-non-leap Feb, midnight-BKK anchor) |
| `test_erp_price_fallback.py` | `_try_load_erp_prices` 4-branch policy (fresh cache, API success+cache write, API fail+stale cache, API fail+no cache) |
| `test_pipeline_smoke.py` | **End-to-end**: fake BQ rows → `aggregate_with_bom` → `_build_rows` → `write_configured_sheet` → openpyxl read-back → cell-level assertions. Final safety net for any refactor. |

## When something fails

A snapshot/index test failing is **the point** — it tells you a business rule
moved. Either:

1. The change is intentional → update the test assertion in the same commit
   so future readers see what rule changed and why.
2. The change is accidental → revert the production change.

Never "just delete" a failing test.

## Refactor checklist (semantic-layer move)

When CTE extraction starts (`semantic/entities/sale_line.py` etc.):

- [ ] All `test_sql_snapshots` still pass against the assembled SQL.
- [ ] `test_aggregate_with_bom` / `test_build_rows` still pass — pure-function
      contracts unchanged.
- [ ] `test_build_rows.YamlIndexCrossCheck` still passes — `columns.yaml`
      stayed in sync.
