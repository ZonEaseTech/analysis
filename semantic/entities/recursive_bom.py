"""套餐 BOM 递归展开 (order-traced 的正确替代, 对齐 ttpos 后端 expandBom)。

背景 (2026-06-13 系统调试 + 对账 MySQL 钉死):
  - 套餐成本之前算不出/出 1.333/0.14, 不是数据缺口 (BQ 与 MySQL 1:1 验证无缺),
    而是我们展开逻辑两个 bug: ① 过滤了软删配方(改菜单删的旧配方, 销售时点是活的)
    ② 没递归(套餐套套餐: 超值6件→超值3人餐→炸鸡)。
  - 用"不过滤软删 + 递归到叶子"重算, 60店抽样套餐成本可算率 ~99.6% (之前误判 50%)。

本实体产出: 每个套餐 item_uuid 的**单份**物料消耗 (递归展开后合并):
  item_uuid -> [(material_code, material_name, bom_num, bom_unit, conversion_rate, material_bq_price)]
与 bom.py / package_consumption 同形, 报表 _bom_for_item 直接消费, 价格走 ERPNext resolver。

取料链 (对齐 ttpos `ProductPackageGroups → GroupItems → ProductBom → Materials`):
  ttpos_product_package_group(g) → ttpos_product_package_group_item(gi)
    每个 gi 槽位: 优先 gi.product_bom_uuid 的料; 没有则递归子品(sub 自己是套餐); 再没有则子品自身 product_bom。
  物料明细: product_bom → related_material(card/uuid 双路) → material, 同 bom.py。

可选组 (3选2): weight = min(1, optional_count / candidate_count), 摊到该组每个槽位
  (期望成本法; 数量近似, 不影响覆盖。固定包材出分数是已知 cosmetic, 见 1.333 备忘)。

软删处理: 结构表 (group/group_item/product_bom) 用 `delete_time=0 OR delete_time>=月初`,
  即"销售月内有效"的配方; 物料表保持 delete_time=0。

注 (2026-06-13): 试过"订单真实频率权重 + 用量/点选率拆列展示", 因组内权重未归一化导致
  成本系统性虚高(套餐负毛利 64→1286), 已回退到本均匀摊销版。正确做法需"组内按真实频率
  归一化使 Σ权重=optional_count + 多路径去重", 留作后续单独立项, 别在本线叠补丁。
"""


def group_item_sql() -> str:
    """每店 group_item (含销售月内有效的软删行)。列: combo/group_uuid/optional_count/sub/pbom/gi_num。"""
    return """
SELECT
  g.product_package_uuid AS combo,
  g.uuid AS group_uuid,
  g.optional_count AS optional_count,
  gi.related_uuid AS sub,
  gi.product_bom_uuid AS pbom,
  gi.num AS gi_num
FROM `{project}`.`{dataset}`.`ttpos_product_package_group` g
JOIN `{project}`.`{dataset}`.`ttpos_product_package_group_item` gi
  ON gi.product_package_group_uuid = g.uuid
WHERE (g.delete_time = 0 OR g.delete_time >= {start_ts})
  AND (gi.delete_time = 0 OR gi.delete_time >= {start_ts})
"""


def bom_material_sql() -> str:
    """每店 product_bom → 物料明细 (含月内有效软删 BOM)。
    列: pb_uuid/pkg/material_code/material_name/bom_num/bom_unit/conversion_rate/material_bq_price。"""
    return """
SELECT
  pb.uuid AS pb_uuid,
  pb.product_package_uuid AS pkg,
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
FROM `{project}`.`{dataset}`.`ttpos_product_bom` pb
JOIN `{project}`.`{dataset}`.`ttpos_related_material` rm
  ON (
    (pb.product_bom_card_uuid > 0 AND rm.related_uuid = pb.product_bom_card_uuid)
    OR (pb.product_bom_card_uuid = 0 AND rm.related_uuid = pb.uuid)
  )
  AND rm.delete_time = 0
JOIN `{project}`.`{dataset}`.`ttpos_material` m
  ON m.uuid = rm.material_uuid AND m.delete_time = 0
WHERE (pb.delete_time = 0 OR pb.delete_time >= {start_ts})
"""


def build_store_recursive_bom(gi_rows, bom_rows, max_depth: int = 6):
    """纯 Python 递归展开。输入两组 dict 列表 (字段见上方 SQL 别名), 输出:
       {combo_item_uuid: [(code, name, bom_num, bom_unit, conv_rate, bq_price), ...]}
    只产出"有 group 的商品"(套餐); 单品由报表 bq_native 路径处理。
    可选组按 weight=optional_count/candidate_count 摊销, 已乘进 bom_num。
    """
    bom_mats: dict[str, list] = {}    # pb_uuid -> [(code,name,num,unit,conv,price)]
    prod_boms: dict[str, set] = {}    # pkg_uuid -> {pb_uuid}
    for r in bom_rows:
        pb = str(r["pb_uuid"])
        conv = r["conversion_rate"]
        bom_mats.setdefault(pb, []).append((
            r["material_code"], r["material_name"], float(r["bom_num"] or 0),
            r["bom_unit"], float(conv) if conv is not None else 1.0, r["material_bq_price"],
        ))
        prod_boms.setdefault(str(r["pkg"]), set()).add(pb)

    # groups: combo -> {group_uuid: {"oc": optional_count, "items": [(sub,pbom,gi_num)]}}
    groups: dict[str, dict] = {}
    for r in gi_rows:
        combo = str(r["combo"]); gu = str(r["group_uuid"])
        g = groups.setdefault(combo, {}).setdefault(
            gu, {"oc": float(r["optional_count"] or 0), "items": []})
        g["items"].append((str(r["sub"]), str(r["pbom"]), float(r["gi_num"] or 1)))

    def mats_of_prod(pkg):
        out = []
        for pb in prod_boms.get(pkg, ()):
            out.extend(bom_mats[pb])
        return out

    def expand(uuid, depth=0, seen=None):
        seen = seen or set()
        if uuid in seen or depth > max_depth:
            return []
        seen = seen | {uuid}
        if uuid not in groups:
            return mats_of_prod(uuid)
        out = []
        for _gu, g in groups[uuid].items():
            cc = len(g["items"]); oc = g["oc"]
            w = min(1.0, oc / cc) if cc and oc > 0 else 1.0
            for sub, pbom, num in g["items"]:
                mats = bom_mats.get(pbom)            # ① 槽位自带配方
                if not mats and sub in groups:
                    mats = expand(sub, depth + 1, seen)  # ② 子品是套餐 → 递归
                if not mats:
                    mats = mats_of_prod(sub)         # ③ 子品自身 product_bom
                mult = num * w
                for code, name, bn, unit, conv, price in mats:
                    out.append((code, name, bn * mult, unit, conv, price))
        return out

    result = {}
    for combo in groups:
        merged = {}
        for code, name, bn, unit, conv, price in expand(combo):
            if not code:
                continue
            if code in merged:
                pn, pbn, punit, pconv, pprice = merged[code]
                merged[code] = (pn or name, pbn + bn, punit or unit, pconv, pprice)
            else:
                merged[code] = (name, bn, unit, conv, price)
        result[combo] = [(c, n, bn, unit, conv, price)
                         for c, (n, bn, unit, conv, price) in merged.items()]
    return result
