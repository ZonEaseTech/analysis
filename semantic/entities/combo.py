"""Combo (套餐) → child product mapping.

ttpos models combos as a parent `sale_order_product` row with `product_type = 1`,
its children link back via `child.package_uuid = parent.uuid`. We can't get the
mapping from product master tables — it's only materialised on actual sales.

Hence this query is time-windowed: it infers the combo structure from the
finished sales (`sb.status = 1`) in the report's reporting period. A combo that
nobody bought in the window will be invisible — which is fine, we only need
mappings for combos we're actually going to compute costs for.
"""


def combo_structure_sql() -> str:
    """Returns the full `SELECT DISTINCT … FROM …` query (a complete statement)."""
    return """
SELECT DISTINCT
  parent_sop.product_package_uuid AS combo_uuid,
  child_sop.product_package_uuid AS child_uuid
FROM `{project}`.`{dataset}`.`ttpos_sale_order_product` parent_sop
JOIN `{project}`.`{dataset}`.`ttpos_sale_order_product` child_sop
  ON child_sop.package_uuid = parent_sop.uuid
  AND child_sop.delete_time = 0
JOIN `{project}`.`{dataset}`.`ttpos_sale_bill` sb
  ON sb.uuid = parent_sop.sale_bill_uuid AND sb.delete_time = 0
WHERE parent_sop.product_type = 1
  AND parent_sop.delete_time = 0
  AND sb.status = 1
  AND sb.finish_time >= {start_ts}
  AND sb.finish_time < {end_ts}
"""
