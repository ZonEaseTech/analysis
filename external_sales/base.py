"""ExternalSalesSource 抽象接口.

每个外部 provider (huku 等) 实现 load() 方法, 返回符合 ExternalSKURow shape
的行列表, 主报表可直接 append 到自己的 SKU rows 后面.

shape 跟 bq_reports/profit_by_price_report.py 的 _build_by_price_rows
输出对齐 (38 列), 缺少的字段填 0 / 空字符串 / 合成值.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class ExternalSKURow:
    """一行 SKU (含 BOM 物料明细). 跟 profit_by_price 的 flat row 一一对应.

    序号对照 (跟 resources/reports/profit_by_price.yaml 的 field_index 一致):
      0  门店编号        store_num: str
      1  门店名称        store_name: str
      2  SKU 名          item_name: str
      3  销量            qty_total: float (外部源没有"含赠/退"概念, 等于 qty_net)
      4  净销量          qty_net: float
      5  营业额          gross_amount: float (= qty × 标价)
      6  标准金额        standard_amount: float (BOM 标价 × 销量, 外部源没数据填 0)
      7  实收金额        revenue: float
      8-10  公式列        留空, Excel 公式自动算
      11 售价1            price_tier1: float (= revenue / qty_net 平均)
      12 净销量1          qty_tier1: float (= qty_net, 单一价格档)
      13-20 售价2-5/净销量2-5  全 0 (外部没价格档拆分)
      21 其它销量        0
      22-25 赠送/退款金额  0 (外部源不区分)
      26 取消数量        0
      27 取消金额        0
      28-32 BOM 5 列     物料 list, 每物料一行 (跟 BQ 一致)
      33-36 利润 4 件套   留空, Excel 公式算
      37 item_uuid       合成 (用 'ext:<hash>' 防止跟 BQ 冲突)
      38 BOM来源
      39 价来源
    """
    store_num: str
    store_name: str
    item_name: str
    category: str  # '单品' / '套餐'
    qty_net: float
    revenue: float
    materials: List[Tuple[str, str, float, float, str]]  # (code, name, qty, unit_price, unit)
    unit_cost: float
    bom_source: str   # "+ 拆解" / strict BOM 名 / "无"
    price_source: str
    match_type: str   # strict / spelling / normalize / split / unmatched
    item_uuid: str = ""

    @property
    def is_unmatched(self) -> bool:
        return self.match_type == "unmatched"


class ExternalSalesSource:
    """外部销售事实表 provider 抽象."""

    def load(self, *, month: str, bq_client=None, merchants=None,
             config=None) -> List[ExternalSKURow]:
        """加载 + 清洗 + (可选) 按 BQ 切换日期切割.

        Args:
            month: 'YYYY-MM' 月份
            bq_client: 用于查 cutover_dates (若 provider 支持日期切割)
            merchants: BQ 商户列表 [(account, uuid, store_num, store_name), ...]
            config: load_config() 返回值, 用于读 store_name_mapping / BOM 等

        Returns:
            List[ExternalSKURow] — 行级 SKU 数据, 已做 BOM 匹配 + 成本展开.
        """
        raise NotImplementedError
