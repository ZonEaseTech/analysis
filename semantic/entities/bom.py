"""Product BOM (bill of materials) entity.

Returns one row per (item_uuid, material_code) with quantity + unit metadata.
Pure product-master data — not joined to orders.

**Soft-delete policy** (encoded in the SQL, easy to break):
ttpos products that get retired set `product_bom.delete_time != 0`, but the
sales tables still reference those uuids for historical transactions.
A naive `WHERE delete_time = 0` filter loses BOMs for those legacy items,
forcing them through the fallback Excel — which is usually wrong.

The window function `active_count` lets us implement:
  - if the product has any active BOM row → keep only active rows
  - if everything is soft-deleted → keep the deleted rows (legacy items)
Equivalent to "active wins, but never drop a product entirely."
"""


def bom_sql() -> str:
    """Returns the full BOM query (a complete `WITH … SELECT …` statement,
    not just a CTE body — callers feed it directly to engine.query)."""
    return """
WITH bom_with_flag AS (
  SELECT
    pb.uuid,
    pb.product_package_uuid,
    pb.product_bom_card_uuid,
    pb.delete_time,
    SUM(CASE WHEN pb.delete_time = 0 THEN 1 ELSE 0 END)
      OVER (PARTITION BY pb.product_package_uuid) AS active_count
  FROM `{project}`.`{dataset}`.`ttpos_product_bom` pb
)
SELECT
  pb.product_package_uuid AS item_uuid,
  m.code AS material_code,
  JSON_EXTRACT_SCALAR(m.name, '$.zh') AS material_name,
  rm.num AS bom_num,
  COALESCE(
    JSON_EXTRACT_SCALAR(rm.unit_name, '$.zh'),
    JSON_EXTRACT_SCALAR(rm.unit_name, '$.en'),
    JSON_EXTRACT_SCALAR(rm.base_unit_name, '$.zh'),
    JSON_EXTRACT_SCALAR(rm.base_unit_name, '$.en')
  ) AS bom_unit,
  rm.base_unit_conversion_rate AS conversion_rate,
  m.price AS material_bq_price
FROM bom_with_flag pb
LEFT JOIN `{project}`.`{dataset}`.`ttpos_related_material` rm
  ON (
    (pb.product_bom_card_uuid > 0 AND rm.related_uuid = pb.product_bom_card_uuid)
    OR (pb.product_bom_card_uuid = 0 AND rm.related_uuid = pb.uuid)
  )
  AND rm.delete_time = 0
LEFT JOIN `{project}`.`{dataset}`.`ttpos_material` m
  ON m.uuid = rm.material_uuid AND m.delete_time = 0
WHERE (pb.delete_time = 0 OR pb.active_count = 0)
"""
