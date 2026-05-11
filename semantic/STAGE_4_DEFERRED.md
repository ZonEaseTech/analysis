# Stage 4 (daily_sales migration) — deferred

**Decision:** the daily_item_sales report cannot reuse the current `sale_line`
/ `takeout_line` entities as-is. Migrating it would require either
over-parametrizing the existing entities or adding a new lower-level
primitive. Neither is appropriate without tests for daily_item_sales first.

## What the recon found

| Aspect | profit_margin (sale_line / takeout_line) | daily_item_sales |
| --- | --- | --- |
| Aggregation grain | one row per item (whole month) | one row per (date, item) |
| Dine-in source table | `ttpos_statistics_product` (pre-aggregated) | `ttpos_sale_order_product` (raw orders) |
| Dine-in time field | `sp.complete_time` | `sb.finish_time` |
| Dine-in row filters | trim by `complete_time` window | extra `cancel_time=0 AND gift_time=0 AND status=1 AND product_type<>1` |
| Takeout state=60 | excluded from sales/revenue, counted separately as `cancelled_qty` | included in the per-day qty (混在销量里) |
| Takeout time field | dynamic (state=40 → `completed_time`, else `accepted_time`) | same |
| Returned columns | 12 metrics (sales_price / actual / refund / cancel / free / give / discount …) | one metric (`qty`) |

The two reports answer different questions on different aggregation grains.
The current entities encode **profit_margin's specific semantics** (split
cancellations out, use the pre-aggregated `statistics_product`, return 12
metrics). Forcing daily_sales through them would either:

- **Over-parametrize**: add args like `cancel_in_qty: bool`, `time_field: …`,
  `source: 'raw'|'aggregated'`. Each new flag is a new bug surface. Within
  six months the entities become "shop_sales(everything_optional)" and stop
  encoding any specific contract.
- **Wrong source**: silently switch daily_sales to `ttpos_statistics_product`
  and hope nobody notices the row-set difference.

Neither is acceptable.

## What WOULD make Stage 4 right

A genuine lower-level primitive — call it `raw_sale_event` — that returns
**unaggregated rows** ((sale_date, item_uuid, qty, …) per transaction line)
from both channels. Both reports then aggregate it differently:

```
semantic/entities/raw_sale_event.py
  dine_sale_events_cte()      # raw rows from statistics_product OR sale_order_product
  takeout_sale_events_cte()   # raw rows from takeout_order_item
```

`profit_margin` would compose: `raw_sale_event` → `shop_sales` aggregation in
its own report-specific CTE.
`daily_item_sales` would compose: `raw_sale_event` → per-day aggregation.

This is genuinely useful but represents a **new design layer**, not a
mechanical extraction. Doing it correctly needs:

1. Snapshot tests for `daily_item_sales` SQL first (none exist today).
2. Agreement on whether the raw primitive uses `statistics_product`
   or `sale_order_product` (they differ in retention / refund treatment).
3. End-to-end byte-equality verification — same as the Stage 1 process.

## Decision

Ship Stages 2 / 3 / 5 (pure extractions, byte-identical SQL, all 84 tests
green) and stop here. Open a follow-up task for `raw_sale_event` design when
someone next needs to touch daily_item_sales.

**Anti-rule for future contributors:** do not "migrate" a report by
adding kwargs to the existing entity functions. If you find yourself adding
a third boolean flag, you're solving the wrong problem — write a new
lower-level primitive instead.
