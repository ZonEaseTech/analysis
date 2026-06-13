# 成本表实收金额矫正 —— (店,价)级真实折扣分摊

> 谁要: 何伟涛 / 2026-06-13
> 目标: 把客户 3 份 ttpos「商品成本毛利分析」表(各 20 店,共 60 店)拼成一份,
> 并把「实收金额」列从**营业额口径**矫正为**实收口径**(扣掉真实营销折扣),
> 其余列字节不变。

## 1. 背景与硬约束(已实测,非推测)

客户成本表的「实收金额」列实际是**营业额**(`product_final_price×(num−refund)`,未扣券)。
真实实收 = 营业额 − 营销折扣。折扣发生在**订单级**,要回溯到商品行。

回溯需要一把"连接钥匙",把 BQ 算出的折扣贴回成本表的行。实测三种钥匙(2026-05 数据):

| 钥匙 | 覆盖/总额守恒 | 能精确到单行 | 否决原因 |
|---|---|---|---|
| 商品名 | 53% | 53% | 名字是 ttpos 加工过(带 `/pc`、删除品),47% 对不上,总额不守恒 |
| (店,价,净销量) | 35% | 35% | 净销量对不齐:成本表把同商品**全渠道合并**,BQ 只算 POS 腿 → 62% 净销量不一致,丢 65% 折扣 |
| **(店,价)** | **100% / 0 丢失** | 11.9% | 同店同价常 10+ 商品,88% 折扣需在它们之间按营业额比例摊 |

**结论:这份成本表无法把折扣精确落到"单个打折商品行"。** 它本身丢了三样:
(1) 没有商品 ID(只有加工中文名);(2) 没有渠道列(堂食+外卖合并一行);(3) 净销量是合并值。

唯一能 100% 对齐、折扣一分不丢的连接维度是 `(店, 价)`。**这是数据允许的天花板。**

## 2. 方案: (店,价)级真实折扣分摊

对每个 `(店号, 单价)`:
- BQ 算出该价格档在该店的**真实折扣总额**(只来自有折扣的订单);
- 把这笔折扣,按**营业额比例**分摊到成本表中该 `(店,价)` 的所有商品行;
- 没有折扣的价格档 → 折扣 = 0,该行**原样不动**(优于店级摊销:店级会把折扣抹到全店每行)。

只动「实收金额」(L 列):`实收 = 营业额(L 原值) − 该行分摊折扣`。其余 15 列字节不变。

## 3. 数据流

```
3 份 xlsx ──XML级保真拼接──► 拼接原始表(60店, 实收=营业额)
                                      │
BQ 60 店并行 ──► (店,价)→真实折扣 ─────┤ 按营业额比例分摊
                                      ▼
                              (店,价)级矫正表(只改 L 列)
```

### 3.1 BQ 折扣算法(每店)

折扣字段 = 7 项之和(经 2026-04/05 桥回归确认,覆盖全部营销减项):
`coupon_amount + member_discount_fee + custom_discount_fee + activity_amount + gift_amount + pay_points_amount + zero_checkout_fee`

```sql
WITH line AS (
  SELECT sp.sale_order_uuid so,
         CAST(ROUND(sp.product_sale_price) AS INT64) price,
         IF(sp.free_num>0 OR sp.give_num>0, 0,
            sp.product_final_price*(sp.product_num-sp.refund_num)) rev
  FROM `ttpos_statistics_product` sp
  WHERE sp.complete_time>={s} AND sp.complete_time<{e}),
od AS (SELECT uuid so, (coupon_amount+member_discount_fee+custom_discount_fee
        +activity_amount+gift_amount+pay_points_amount+zero_checkout_fee) disc
       FROM `ttpos_sale_order`),
ot AS (SELECT so, SUM(rev) tot FROM line GROUP BY so)
SELECT l.price, SUM(od.disc*SAFE_DIVIDE(l.rev, NULLIF(ot.tot,0))) disc
FROM line l JOIN od ON od.so=l.so JOIN ot ON ot.so=l.so
GROUP BY l.price        -- 只保留 disc>0.005
```
- 订单按 `statistics_product.sale_order_uuid` 关联(沿用 SP 的时间窗),不用 sale_order 自己的时间;
- 价格 `ROUND` 取整,与成本表 E 列对齐(实测 (店,价) 100% 命中);
- 折扣在订单内按"商品行营业额 / 订单总营业额"分摊到价格档。

### 3.2 拼接(复用现有 XML 级逻辑)

沿用 `merge3_correct_revenue.py` 已验证的字符串级拼接:
styles.xml 三份 md5 相同断言、行号连续/唯一断言、行数+合并数恒等、跳表头行、跳表头合并、
无 `t="s"`/无 `<f>` 断言、dimension 更新。成本表表头(row 1)与商品块合并单元格原样保留。

### 3.3 矫正 L 列

遍历拼接表每一行(跳 row 1 表头):
1. 商品块**首行**(A 列非空)读 `店号 = A.split()[0]`、`价 = E`、`营业额 = L`;
2. `行折扣 = (店,价)总折扣 × 行营业额 / Σ(同(店,价)行营业额)`;
3. `新 L = round(营业额 − 行折扣, 2)`;
4. (店,价) 无折扣 → 行折扣 0 → L 不变。

> 注: 需先扫一遍拼接表,按 (店,价) 汇总各行营业额,得分摊分母;再回写。

## 4. 验证(导出阶段强制打印)

| 校验 | 期望 | 含义 |
|---|---|---|
| 扣的折扣总额 | = BQ 折扣 123,848 ±1 | 守恒,一分不丢 |
| 除 L 列外字节 | 两表 maskL 后完全相同 | 只动了实收 |
| 行数 / 合并数 | 拼接前后恒等 | 保真 |
| 矫正后实收合计 | ≈ 营业额 − 123,848 ≈ 28,887,214 | 对账锚 |
| vs 营业额汇总 28,887,524 | 差 ~310 | 残差=退货/外卖口径差,非折扣,**不分摊**(诚实保留) |
| 逐店 | Σ每店折扣 = BQ每店折扣 | 每店守恒 |

**残差说明(诚实):** 矫正后 ≈ 28,887,214,与汇总表 28,887,524 仍差几百元。
这几百是**退货口径差 + 外卖口径差**(见 `recon_cost_vs_summary_bridge.py`),
**不是营销折扣**,不该硬塞进商品行。保留为残差,不污染数据。

## 5. 交付物

1. `exports/成本毛利分析_5月_拼接原始_60店.xlsx` —— 3 份保真拼接,实收=营业额(原值);
2. `exports/成本毛利分析_5月_实收已矫正_60店.xlsx` —— (店,价)级真实折扣分摊,只改 L 列。

两份除「实收金额」列外字节相同。导出脚本: `scripts/adhoc/merge3_pricelevel_discount.py`。

## 6. 不做(YAGNI)

- 不追求商品行级精确(数据不支持,见 §1);
- 不把退货/外卖口径残差摊进商品行(非折扣);
- 不改任何 Python 业务代码,不动 config.yaml(纯 adhoc 交付脚本)。
