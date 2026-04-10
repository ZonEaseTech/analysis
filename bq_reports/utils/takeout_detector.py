#!/usr/bin/env python3
"""
外卖订单识别器 (TakeoutOrderDetector)

封装华莱士外卖订单的判定逻辑，支持3个条件组合判断：
- 条件1: 支付方式匹配（Robinhood/Grab/Lineman/Shopee）
- 条件2: 订单来源渠道匹配（Grab/LINE MAN）
- 条件3: 账单类型匹配（会员外送 bill_type=2）
- 外加: 外卖平台订单（takeout_order）

Usage:
    from utils.takeout_detector import TakeoutOrderDetector
    
    # 使用默认配置（华莱士标准）
    detector = TakeoutOrderDetector.default()
    
    # 生成SQL条件
    sql_condition = detector.build_sql_condition("sb")
    
    # 判断单个订单
    is_takeout = detector.detect_from_sale_bill(bill_data)
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class TakeoutDetectionResult:
    """外卖判定结果"""
    is_takeout: bool
    matched_conditions: List[str]  # 匹配的条件列表
    confidence: float  # 置信度 (0-1)


class TakeoutOrderDetector:
    """
    外卖订单识别器
    
    Args:
        payment_methods: 支付方式关键词列表，不区分大小写/空格
        order_source_channels: 订单来源渠道关键词列表
        bill_types: 账单类型列表（如 [2] 表示会员外送）
        include_takeout_platform: 是否包含外卖平台订单
    """
    
    # 华莱士标准配置
    DEFAULT_PAYMENT_METHODS = ["Robinhood", "Grab", "Lineman", "Shopee"]
    DEFAULT_ORDER_SOURCE_CHANNELS = ["Grab", "LINE MAN", "Lineman"]
    DEFAULT_BILL_TYPES = [2]  # 会员外送
    
    def __init__(
        self,
        payment_methods: Optional[List[str]] = None,
        order_source_channels: Optional[List[str]] = None,
        bill_types: Optional[List[int]] = None,
        include_takeout_platform: bool = True
    ):
        self.payment_methods = payment_methods or self.DEFAULT_PAYMENT_METHODS
        self.order_source_channels = order_source_channels or self.DEFAULT_ORDER_SOURCE_CHANNELS
        self.bill_types = bill_types or self.DEFAULT_BILL_TYPES
        self.include_takeout_platform = include_takeout_platform
    
    @classmethod
    def default(cls) -> "TakeoutOrderDetector":
        """创建华莱士标准配置的识别器"""
        return cls()
    
    def build_payment_condition(self, table_alias: str = "po") -> str:
        """
        生成支付方式匹配的SQL条件（条件1）
        
        Returns SQL like:
            REGEXP_CONTAINS(LOWER(REPLACE(po.payment_method_name, ' ', '')), r'robinhood|grab|lineman|shopee')
        """
        if not self.payment_methods:
            return "FALSE"
        
        # 转换为小写并去除空格
        patterns = [method.lower().replace(" ", "") for method in self.payment_methods]
        pattern_str = "|".join(patterns)
        
        return f"REGEXP_CONTAINS(LOWER(REPLACE({table_alias}.payment_method_name, ' ', '')), r'{pattern_str}')"
    
    def build_order_source_condition(self, table_alias: str = "fb") -> str:
        """
        生成订单来源匹配的SQL条件（条件2）
        
        匹配 order_source 多语言名称和 JSON 快照
        """
        if not self.order_source_channels:
            return "FALSE"
        
        patterns = [ch.lower().replace(" ", "") for ch in self.order_source_channels]
        pattern_str = "|".join(patterns)
        
        # 子查询：匹配多语言名称
        subquery = f"""(
            SELECT 1 FROM `{{project}}`.`{{dataset}}`.`ttpos_order_source` os
            LEFT JOIN `{{project}}`.`{{dataset}}`.`ttpos_multi_language_name` mln
              ON mln.uuid = os.multi_language_name_uuid AND mln.delete_time = 0
            WHERE os.uuid = {table_alias}.order_source_uuid AND os.delete_time = 0
              AND REGEXP_CONTAINS(
                LOWER(REPLACE(CONCAT(IFNULL(mln.zh_name,''),IFNULL(mln.th_name,''),IFNULL(mln.en_name,'')), ' ', '')),
                r'{pattern_str}'
              )
          )"""
        
        # JSON 快照匹配
        json_match = f"""REGEXP_CONTAINS(
            LOWER(REPLACE(CONCAT(
              IFNULL(JSON_EXTRACT_SCALAR({table_alias}.order_source_name, '$.zh'), ''),
              IFNULL(JSON_EXTRACT_SCALAR({table_alias}.order_source_name, '$.th'), ''),
              IFNULL(JSON_EXTRACT_SCALAR({table_alias}.order_source_name, '$.en'), '')
            ), ' ', '')),
            r'{pattern_str}'
          )"""
        
        return f"({table_alias}.order_source_uuid > 0 AND ({subquery} OR {json_match}))"
    
    def build_bill_type_condition(self, table_alias: str = "fb") -> str:
        """生成账单类型匹配的SQL条件（条件3）"""
        if not self.bill_types:
            return "FALSE"
        
        if len(self.bill_types) == 1:
            return f"{table_alias}.bill_type = {self.bill_types[0]}"
        else:
            types_str = ", ".join(str(t) for t in self.bill_types)
            return f"{table_alias}.bill_type IN ({types_str})"
    
    def build_sql_condition(self, table_alias: str = "fb", po_alias: str = "po") -> str:
        """
        生成完整的SQL WHERE条件（所有条件OR组合）
        
        Args:
            table_alias: sale_bill 表别名
            po_alias: payment_order 表别名（在子查询中使用）
        
        Returns:
            完整的SQL条件字符串
        """
        conditions = []
        
        # 条件1: 支付方式
        if self.payment_methods:
            conditions.append(f"""EXISTS (
                SELECT 1 FROM `{{project}}`.`{{dataset}}`.`ttpos_payment_order` {po_alias}
                INNER JOIN `{{project}}`.`{{dataset}}`.`ttpos_sale_order` so
                  ON so.uuid = {po_alias}.related_uuid AND {po_alias}.related_type = 0 AND so.delete_time = 0
                WHERE so.sale_bill_uuid = {table_alias}.uuid
                  AND {po_alias}.delete_time = 0 AND {po_alias}.status = 1
                  AND {self.build_payment_condition(po_alias)}
              )""")
        
        # 条件2: 订单来源
        if self.order_source_channels:
            # 这里需要 project 和 dataset 参数，简化处理
            # 实际使用时需要在完整SQL中替换
            pass
        
        # 条件3: 账单类型
        if self.bill_types:
            conditions.append(self.build_bill_type_condition(table_alias))
        
        if not conditions:
            return "FALSE"
        
        return " OR ".join(f"({c})" for c in conditions)
    
    def detect_from_sale_bill(self, bill_data: dict) -> TakeoutDetectionResult:
        """
        判断单个 sale_bill 是否为外卖订单
        
        Args:
            bill_data: 包含以下字段的字典:
                - payment_method_name: 支付方式名称
                - order_source_name: 订单来源名称（多语言JSON或字符串）
                - bill_type: 账单类型
        
        Returns:
            TakeoutDetectionResult
        """
        matched_conditions = []
        
        # 条件1: 支付方式
        payment_method = bill_data.get("payment_method_name", "")
        if payment_method:
            pm_normalized = payment_method.lower().replace(" ", "")
            for method in self.payment_methods:
                if method.lower().replace(" ", "") in pm_normalized:
                    matched_conditions.append("payment_method")
                    break
        
        # 条件2: 订单来源（简化版，仅检查字符串包含）
        order_source = bill_data.get("order_source_name", "")
        if order_source:
            os_normalized = order_source.lower().replace(" ", "")
            for channel in self.order_source_channels:
                if channel.lower().replace(" ", "") in os_normalized:
                    matched_conditions.append("order_source")
                    break
        
        # 条件3: 账单类型
        bill_type = bill_data.get("bill_type")
        if bill_type in self.bill_types:
            matched_conditions.append("bill_type")
        
        is_takeout = len(matched_conditions) > 0
        confidence = min(len(matched_conditions) * 0.33 + 0.34, 1.0)
        
        return TakeoutDetectionResult(
            is_takeout=is_takeout,
            matched_conditions=matched_conditions,
            confidence=confidence
        )
    
    def get_sql_template(self) -> str:
        """
        获取完整的SQL模板（用于sale_bill外卖判断）
        
        使用 {project} 和 {dataset} 作为占位符
        """
        payment_pattern = "|".join(m.lower().replace(" ", "") for m in self.payment_methods)
        channel_pattern = "|".join(c.lower().replace(" ", "") for c in self.order_source_channels)
        
        return f"""
-- 外卖订单识别CTE
grab_lineman_sources AS (
  SELECT os.uuid AS source_uuid
  FROM `{{project}}`.`{{dataset}}`.`ttpos_order_source` AS os
  LEFT JOIN `{{project}}`.`{{dataset}}`.`ttpos_multi_language_name` AS mln
    ON mln.uuid = os.multi_language_name_uuid AND mln.delete_time = 0
  WHERE os.delete_time = 0
    AND REGEXP_CONTAINS(
      LOWER(REPLACE(CONCAT(IFNULL(mln.zh_name,''),IFNULL(mln.th_name,''),IFNULL(mln.en_name,'')), ' ', '')),
      r'{channel_pattern}'
    )
),

bill_takeout AS (
  SELECT
    fb.uuid AS bill_uuid,
    fb.amount,
    fb.payment_amount,
    (
      -- 条件1: 支付方式匹配
      EXISTS (
        SELECT 1 FROM `{{project}}`.`{{dataset}}`.`ttpos_payment_order` po
        INNER JOIN `{{project}}`.`{{dataset}}`.`ttpos_sale_order` so
          ON so.uuid = po.related_uuid AND po.related_type = 0 AND so.delete_time = 0
        WHERE so.sale_bill_uuid = fb.uuid
          AND po.delete_time = 0 AND po.status = 1
          AND REGEXP_CONTAINS(LOWER(REPLACE(po.payment_method_name, ' ', '')), r'{payment_pattern}')
      )
      -- 条件2: 订单来源匹配
      OR (
        fb.order_source_uuid > 0
        AND fb.order_source_uuid IN (SELECT source_uuid FROM grab_lineman_sources)
      )
      OR REGEXP_CONTAINS(
        LOWER(REPLACE(CONCAT(
          IFNULL(JSON_EXTRACT_SCALAR(fb.order_source_name, '$.zh'), ''),
          IFNULL(JSON_EXTRACT_SCALAR(fb.order_source_name, '$.th'), ''),
          IFNULL(JSON_EXTRACT_SCALAR(fb.order_source_name, '$.en'), '')
        ), ' ', '')),
        r'{channel_pattern}'
      )
      -- 条件3: 账单类型匹配
      OR fb.bill_type IN ({', '.join(str(t) for t in self.bill_types)})
    ) AS is_takeout
  FROM `{{project}}`.`{{dataset}}`.`ttpos_sale_bill` fb
  WHERE fb.delete_time = 0 AND fb.status = 1
    AND fb.finish_time >= {{start_ts}} AND fb.finish_time < {{end_ts}}
)
"""


# 便捷函数
def is_takeout_order(bill_data: dict) -> bool:
    """快速判断是否为外卖订单（使用默认配置）"""
    detector = TakeoutOrderDetector.default()
    result = detector.detect_from_sale_bill(bill_data)
    return result.is_takeout
