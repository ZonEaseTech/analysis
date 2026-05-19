"""外部销售事实表接入 — 客户曾用过的别家 POS / 总部口径报表.

主报表 (bq_reports/profit_by_price_report.py) 通过 --external <spec> 选项可选启用.
不传 --external 时主报表行为零变化, 跟纯 BQ 跑结果一致.

未来不再需要某外部源时, 删 external_sales/<provider>/ 整个目录即可.

当前支持:
  - huku  弧酷 (前任 POS, 2026-01 切换前的历史数据)
"""
from external_sales.base import ExternalSalesSource, ExternalSKURow
from external_sales.huku.loader import HukuLoader


def load_external(spec: str, *, bq_client=None, merchants=None,
                  month: str, config=None):
    """根据 spec 加载外部数据源.

    spec 格式: 'huku:path=<xlsx>'  (provider:k=v;k=v)

    Returns:
        list[ExternalSKURow] — 同 profit_by_price 报表 SKU 行 schema, 可直接 append.
    """
    if ":" not in spec:
        raise ValueError(f"invalid --external spec: {spec!r} (expect 'provider:key=val')")
    provider, kvs = spec.split(":", 1)
    opts = {}
    for kv in kvs.split(";"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            opts[k.strip()] = v.strip()
    provider = provider.strip().lower()
    if provider == "huku":
        return HukuLoader(opts["path"]).load(
            month=month, bq_client=bq_client, merchants=merchants, config=config,
        )
    raise ValueError(f"unknown external provider: {provider!r}")


def to_profit_by_price_rows(ext_rows, mode: str):
    """ExternalSKURow → flat 45-col rows, 同 _build_by_price_rows 输出 shape.

    Args:
        ext_rows: list[ExternalSKURow]
        mode: 'combo' (取 category='套餐') 或 'single' (取 category='单品')

    Returns:
        list[list] — 每行 45 列, 直接 extend 进主报表 rows 即可.
    """
    cat_filter = "套餐" if mode == "combo" else "单品"
    flat = []
    for r in ext_rows:
        if r.category != cat_filter:
            continue
        # prefix (列 0-27)
        avg_price = round(r.revenue / r.qty_net, 2) if r.qty_net else None
        prefix = [
            r.store_num, r.store_name, r.item_name,
            round(r.qty_net, 2),                 # 3 销量 (外部没赠/退/取, 等于净销量)
            round(r.qty_net, 2),                 # 4 净销量
            round(r.revenue, 2),                 # 5 营业额 (用实收近似)
            0,                                   # 6 标准金额 (外部没数据)
            round(r.revenue, 2),                 # 7 实收
            None, None, None,                    # 8-10 公式
            avg_price,                           # 11 售价1 (avg)
            round(r.qty_net, 2),                 # 12 净销量1
            None, None, None, None, None, None, None, None,  # 13-20 价格档 2-5
            0,                                   # 21 其它销量
            0, 0, 0, 0, 0, 0,                    # 22-27 赠/退/取消
        ]
        # suffix (列 33-44)
        suffix = [
            None, None, None, None,              # 33-36 利润 4 件套 (公式)
            r.item_uuid,                         # 37 hidden uuid (合成 'ext:...')
            r.bom_source,                        # 38 BOM 来源
            r.price_source,                      # 39 价来源
            0, 0, 0, None, None,                 # 40-44 净利润相关 (外部源不算)
        ]
        if not r.materials:
            flat.append(prefix + ["-", "-", None, None, "-"] + suffix)
        else:
            for code, mname, qty, p, unit in r.materials:
                flat.append(prefix + [
                    mname, code, round(qty, 4), round(p, 4), unit or "-",
                ] + suffix)
    return flat


__all__ = [
    "load_external", "to_profit_by_price_rows",
    "ExternalSalesSource", "ExternalSKURow", "HukuLoader",
]
