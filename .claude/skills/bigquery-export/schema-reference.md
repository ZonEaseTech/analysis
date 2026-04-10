# TTPOS BigQuery Schema 速查

> 所有表前缀 `ttpos_`，BigQuery 中引用格式：`` `{project}`.`{dataset}`.`ttpos_{table}` ``
> 所有表均有 `uuid`(主键)、`create_time`、`update_time`、`delete_time`(软删除) 字段
> 多语言字段存储 JSON：`{"zh":"中文","th":"ไทย","en":"English"}`

---

## 商品与 BOM 体系

### 关系链

```
product_package (商品) 1──N product_bom (规格/BOM)
product_bom N──1 product_bom_card (成本卡)
product_bom_card 1──N related_material (组成材料)
related_material N──1 material (物品/原料)
product_package N──1 product_category (分类)
product_package N──1 supplier (供应商)
material N──1 product_category (分类)
material N──1 supplier (供应商)
```

### ttpos_product_package（商品）

| 字段 | 类型 | 说明 | JSON? |
|------|------|------|-------|
| name | text | 商品名称 | ✅ |
| erp_code | varchar | ERP 编码 | |
| category_uuid | bigint | 分类 ID | |
| supplier_uuid | bigint | 供应商 ID | |
| status | tinyint | 0=上架 1=下架 | |
| image_file_uuid | bigint | 图片 ID | |
| unit_uuid | bigint | 单位 ID | |
| num_type | int | 0=整数 1=小数 | |
| sort | int | 排序 | |

### ttpos_product_bom（商品规格/BOM）

| 字段 | 类型 | 说明 | JSON? |
|------|------|------|-------|
| name | text | 规格名称（不用于业务显示） | ✅ |
| price | decimal(12,2) | 价格 | |
| purchase_price | decimal(12,2) | 采购单价 | |
| erp_code | varchar | 商品编码 | |
| stock_num | decimal(12,4) | 库存数量 | |
| barcode_value | varchar | 条形码 | |
| product_package_uuid | bigint | → product_package.uuid | |
| product_flavor_uuid | bigint | → product_flavor.uuid（仅商品） | |
| product_sauce_uuid | bigint | → product_sauce.uuid（仅小料） | |
| product_bom_card_uuid | bigint | → product_bom_card.uuid | |
| status | tinyint | 0=下架 1=上架 | |
| is_sold_out | tinyint | 0=否 1=沽清 | |
| actual_sale_num | decimal(12,4) | 实际销量 | |

### ttpos_product_bom_card（成本卡）

| 字段 | 类型 | 说明 | JSON? |
|------|------|------|-------|
| name | text | 成本卡名称 | ✅ |
| erp_code | varchar | ERP 编码 | |
| num | decimal(14,4) | 加工份数 | |
| is_used | int | 0=未使用 1=已使用 | |
| headquarter_uuid | bigint | 总部 ID（0=门店自建） | |

### ttpos_related_material（关联材料/BOM 组成）

| 字段 | 类型 | 说明 | JSON? |
|------|------|------|-------|
| related_uuid | bigint | → product_bom_card.uuid 或 product_bom.uuid | |
| material_uuid | bigint | → material.uuid | |
| num | decimal(12,4) | 用量 | |
| unit_uuid | bigint | 单位 ID | |
| unit_name | text | 单位名称 | ✅ |
| base_unit_uuid | bigint | 基准单位 ID | |
| base_unit_name | text | 基准单位名称 | ✅ |
| base_unit_conversion_rate | decimal(12,4) | 基准单位转换率 | |

### ttpos_material（物品/原料）

| 字段 | 类型 | 说明 | JSON? |
|------|------|------|-------|
| name | text | 物品名称 | ✅ |
| code | varchar | 物品编码 | |
| stock_num | decimal | 库存数量 | |
| price | decimal | 采购单价 | |
| category_uuid | bigint | 分类 ID | |
| supplier_uuid | bigint | 供应商 ID | |
| unit_uuid | bigint | 基准单位 ID | |
| status | bool | true=上架 false=下架 | |
| barcode_value | varchar | 条形码 | |
| internal_code | varchar | 内部编码 | |
| headquarter_uuid | bigint | 总部 ID | |
| safety_stock | decimal | 安全库存 | |

---

## 订单体系

### 关系链

```
sale_bill (销售账单) 1──N sale_order (销售订单)
sale_order 1──N sale_order_product (订单商品)
sale_bill 1──N payment_order (支付记录)
```

### ttpos_sale_bill（销售账单/主订单）

| 字段 | 类型 | 说明 |
|------|------|------|
| order_no | varchar | 账单编号 |
| duty_no | varchar | 当班编号 |
| serial_no | varchar | 桌位编号/流水号 |
| status | tinyint | 0=待付款 1=已完成 2=已取消 |
| bill_type | tinyint | 0=桌台订单 1=点餐订单 2=会员端外送 |
| dining_method | tinyint | 0=堂食 1=打包 |
| source | int | 0=默认 1=收银机 2=助手 3=平板 4=H5 5=会员端 |
| meal_num | int | 就餐人数 |
| amount | decimal(12,2) | 订单总金额 |
| origin_amount | decimal(12,2) | 折前金额 |
| product_amount | decimal(12,2) | 商品金额 |
| service_fee | decimal(12,2) | 服务费 |
| tax_fee | decimal(12,2) | 税费 |
| payment_amount | decimal(12,2) | 支付金额 |
| desk_uuid | bigint | 桌台 ID |
| member_uuid | bigint | 会员 ID |
| is_buffet | tinyint | 0=否 1=自助餐 |
| finish_time | int(10) | 完成时间戳 |

### ttpos_sale_order（销售订单/子订单）

| 字段 | 类型 | 说明 |
|------|------|------|
| order_no | varchar | 订单编号 |
| status | tinyint | 0=未结账 1=已结账 |
| sale_bill_uuid | bigint | → sale_bill.uuid |
| amount | decimal(12,2) | 应收金额(折后) |
| origin_amount | decimal(12,2) | 原始应收(折前) |
| product_amount | decimal(12,2) | 商品金额 |
| service_fee | decimal(12,2) | 服务费 |
| tax_fee | decimal(12,2) | 税费 |
| member_discount_fee | decimal(12,2) | 会员折扣金额 |
| custom_discount_fee | decimal(12,2) | 自定义折扣金额 |
| finish_time | int(10) | 完成时间戳 |

### ttpos_sale_order_product（订单商品明细）

| 字段 | 类型 | 说明 | JSON? |
|------|------|------|-------|
| name | text | 商品名称快照 | ✅ |
| flavor_name | text | 规格名称快照 | ✅ |
| num | int | 商品数量 | |
| sale_price | decimal(12,2) | 销售价(折前) | |
| price | decimal(12,2) | 最终单价(折后) | |
| total_price | decimal(12,2) | 应收金额(单商品) | |
| product_price | decimal(22,4) | 原始单价 | |
| flavor_price | decimal(12,2) | 规格原价 | |
| sauce_price | decimal(12,2) | 小料价 | |
| discount_fee | decimal(12,2) | 打折金额 | |
| sale_order_uuid | bigint | → sale_order.uuid | |
| product_bom_uuid | bigint | → product_bom.uuid | |
| product_package_uuid | bigint | → product_package.uuid | |
| status | tinyint | 0=未送厨 1=已送厨 | |

---

## 会员体系

### ttpos_member（会员）

| 字段 | 类型 | 说明 |
|------|------|------|
| member_no | varchar | 会员编号 |
| nickname | varchar | 昵称 |
| phone | varchar | 电话 |
| gender | tinyint | 0=女 1=男 2=未知 |
| point | decimal(12,2) | 积分 |
| balance | decimal(12,2) | 余额 |
| gift_balance | decimal(12,2) | 赠送余额 |
| accumulated_recharge_amount | decimal(12,2) | 累计充值 |
| accumulated_consumption_amount | decimal(12,2) | 累计消费 |
| consumption_count | int | 消费次数 |
| member_level_uuid | bigint | 等级 ID |
| birthday | int(10) | 生日时间戳 |

### ttpos_member_recharge_order（充值订单）

| 字段 | 类型 | 说明 |
|------|------|------|
| order_no | varchar | 充值订单编号 |
| status | tinyint | 0=待付款 1=已完成 2=已取消 |
| amount | decimal(12,2) | 交易金额 |
| recharge_amount | decimal(12,2) | 充值金额 |
| gift_amount | decimal(12,2) | 赠送金额 |
| gift_point | decimal(12,2) | 赠送积分 |
| member_uuid | bigint | → member.uuid |
| payment_time | int(10) | 支付时间戳 |
| balance | decimal(12,2) | 充值前余额 |
| balance_recharged | decimal(12,2) | 充值后余额 |

### ttpos_member_point_log（积分变动记录）

| 字段 | 类型 | 说明 |
|------|------|------|
| member_uuid | bigint | → member.uuid |
| scene | tinyint | 10=充值 20=订单赠送 30=管理员 40=退款扣除 60=反结账 70=充值赠送 80=充值反结账 90=扣减 |
| value | decimal(12,2) | 数值（正=加 负=减） |
| describe | varchar | 变动描述 |
| related_uuid | bigint | 关联业务 ID |

---

## 库存盘点体系

### 关系链

```
stock_reconciliation (盘点单) 1──N stock_reconciliation_item (盘点物品明细)
stock_reconciliation_item N──1 material (物品)
stock_reconciliation_item 1──N stock_reconciliation_item_unit (盘点物品单位明细)
```

### ttpos_stock_reconciliation（盘点单）

| 字段 | 类型 | 说明 |
|------|------|------|
| order_no | varchar | 单据编号 |
| erp_code | varchar | ERP 盘点单号 |
| type | int | 1=指定物品 2=全部物品 3=日盘 4=周盘 **5=月盘** 6=固定资产 |
| warehouse_uuid | bigint | 仓库 ID |
| purpose | int | 1=库存盘点 2=期初盘点 |
| status | int | 0=已保存 1=已提交 **2=已审核** 3=已驳回 |
| submit_time | int | 提交时间戳 |
| submitter_staff_uuid | bigint | 发起人 |

### ttpos_stock_reconciliation_item（盘点物品明细）

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_reconciliation_uuid | bigint | → stock_reconciliation.uuid |
| material_uuid | bigint | → material.uuid |
| material_name | text | 物品名称（JSON 备份） |
| **booked_quantity** | decimal(22,4) | 账面库存（基准单位） |
| **counted_quantity** | decimal(22,4) | **实盘数量**（基准单位） |

### ttpos_stock_reconciliation_item_unit（盘点物品单位明细）

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_reconciliation_item_uuid | bigint | → stock_reconciliation_item.uuid |
| material_unit_uuid | bigint | 单位 ID |
| material_unit_name | text | 单位名称（JSON） |
| quantity | decimal(22,4) | 单位数量 |

---

## 调拨体系

### 关系链

```
transfer_order (调拨单) 1──N transfer_order_item (调拨物品)
transfer_order_item 1──N transfer_order_item_unit (调拨物品单位)
transfer_order_item N──1 material (物品)
```

### ttpos_transfer_order（调拨单）

| 字段 | 类型 | 说明 |
|------|------|------|
| order_no | varchar | 单据编号 TR+12位 |
| **transfer_type** | int | **1=调入 2=调出** |
| status | int | 0=待提交 1=待审核 2=已驳回 3=待收货 **4=已完成** |
| company_uuid | bigint | 所属门店 |
| headquarter_uuid | bigint | 总部 |
| sender_company_uuid | bigint | 发货门店 |
| receiver_company_uuid | bigint | 收货门店 |
| order_time | bigint | 单据日期时间戳 |
| submit_time | bigint | 提交时间戳 |

### ttpos_transfer_order_item（调拨物品明细）

| 字段 | 类型 | 说明 |
|------|------|------|
| transfer_order_uuid | bigint | → transfer_order.uuid |
| material_uuid | bigint | → material.uuid |
| material_code | varchar | 物品编码 |
| material_name | text | 物品名称（JSON） |
| valuation | decimal(20,8) | 估值单价（基准单位） |

### ttpos_transfer_order_item_unit（调拨物品单位明细）

| 字段 | 类型 | 说明 |
|------|------|------|
| item_uuid | bigint | → transfer_order_item.uuid |
| transfer_order_uuid | bigint | → transfer_order.uuid |
| unit_uuid | bigint | 单位 ID |
| unit_name | text | 单位名称（JSON） |
| unit_conversion_rate | decimal(12,4) | 单位转换率 |
| num | decimal(22,4) | 调拨数量（该单位） |
| erpnext_uom | varchar | ERP 单位 |

> **调入基准单位数量** = SUM(num * unit_conversion_rate) per item

---

## 消耗体系（销售出库）

### ttpos_sale_order_material（订单原料消耗）

| 字段 | 类型 | 说明 |
|------|------|------|
| sale_order_uuid | bigint | → sale_order.uuid |
| sale_bill_uuid | bigint | → sale_bill.uuid |
| material_uuid | bigint | → material.uuid |
| warehouse_uuid | bigint | 仓库 ID |
| **num** | decimal(12,2) | **消耗数量**（基准单位） |
| is_summarized | int | 0=未统计 1=已统计 |

> 消耗数量关联已完成的订单，按时间范围和门店聚合即可

---

## 辅助表

### ttpos_product_category（商品分类）

| 字段 | 类型 | 说明 | JSON? |
|------|------|------|-------|
| name | varchar | 分类名称 | ✅ |
| parent_uuid | bigint | 父分类 ID | |
| status | tinyint | 1=开启 0=关闭 | |
| code | varchar | 分类编码 | |
| headquarter_uuid | bigint | 总部 ID | |

### ttpos_supplier（供应商）

| 字段 | 类型 | 说明 |
|------|------|------|
| name | varchar | 供应商名称 |
| code | varchar | 编码 |
| erp_code | varchar | ERP 编码 |
| contact_name | varchar | 联系人 |
| contact_phone | varchar | 联系电话 |
| status | int | 0=禁用 1=启用 |
| headquarter_uuid | bigint | 总部 ID |

---

## 常用 JOIN 模式

### BOM 完整链路

```sql
FROM ttpos_product_bom bom
JOIN ttpos_product_package pp ON pp.uuid = bom.product_package_uuid AND pp.delete_time = 0
JOIN ttpos_product_bom_card card ON card.uuid = bom.product_bom_card_uuid AND card.delete_time = 0
JOIN ttpos_related_material rm ON rm.related_uuid = card.uuid AND rm.delete_time = 0
JOIN ttpos_material m ON m.uuid = rm.material_uuid AND m.delete_time = 0
WHERE bom.delete_time = 0
```

### 订单完整链路

```sql
FROM ttpos_sale_bill sb
JOIN ttpos_sale_order so ON so.sale_bill_uuid = sb.uuid AND so.delete_time = 0
JOIN ttpos_sale_order_product sop ON sop.sale_order_uuid = so.uuid AND sop.delete_time = 0
WHERE sb.delete_time = 0
```

### 月盘实盘数据

```sql
FROM ttpos_stock_reconciliation sr
JOIN ttpos_stock_reconciliation_item sri ON sri.stock_reconciliation_uuid = sr.uuid AND sri.delete_time = 0
JOIN ttpos_material m ON m.uuid = sri.material_uuid AND m.delete_time = 0
WHERE sr.delete_time = 0
  AND sr.type = 5              -- 月盘
  AND sr.status = 2            -- 已审核
  AND sr.submit_time BETWEEN {start_ts} AND {end_ts}
-- 取 counted_quantity 为实盘数量（基准单位）
```

### 调入数量

```sql
FROM ttpos_transfer_order t_ord
JOIN ttpos_transfer_order_item ti ON ti.transfer_order_uuid = t_ord.uuid AND ti.delete_time = 0
JOIN ttpos_transfer_order_item_unit tiu ON tiu.item_uuid = ti.uuid AND tiu.delete_time = 0
WHERE t_ord.delete_time = 0
  AND t_ord.transfer_type = 1  -- 调入
  AND t_ord.status = 4         -- 已完成
  AND t_ord.order_time BETWEEN {start_ts} AND {end_ts}
-- 调入基准单位数量 = SUM(tiu.num * tiu.unit_conversion_rate) GROUP BY ti.material_uuid
```

### 消耗数量

```sql
FROM ttpos_sale_order_material som
JOIN ttpos_sale_order so ON so.uuid = som.sale_order_uuid AND so.delete_time = 0
WHERE som.delete_time = 0
  AND so.status = 1            -- 已结账
  AND so.finish_time BETWEEN {start_ts} AND {end_ts}
-- 消耗 = SUM(som.num) GROUP BY som.material_uuid
```

### 商品带分类和供应商

```sql
FROM ttpos_product_package pp
LEFT JOIN ttpos_product_category c1 ON c1.uuid = pp.category_uuid AND c1.delete_time = 0
LEFT JOIN ttpos_product_category c2 ON c2.uuid = c1.parent_uuid AND c2.delete_time = 0
LEFT JOIN ttpos_supplier s ON s.uuid = pp.supplier_uuid AND s.delete_time = 0
WHERE pp.delete_time = 0
```
