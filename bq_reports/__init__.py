"""
BigQuery 报表工具模块

提供从 BigQuery 导出 TTPOS 数据报表的功能。
"""

from .utils.bq_exporter import (
    BaseExporter,
    MultiShopExporter,
    ReportExporter,
    TakeoutRevenueExporter,
    ExportResult,
    ExcelConfig,
)
from .utils.validators import (
    DataValidator,
    ValidationChain,
    ValidationResult,
    create_default_validators,
)
from .utils.sql_templates import get_template, list_templates

__all__ = [
    'BaseExporter',
    'MultiShopExporter',
    'ReportExporter',
    'TakeoutRevenueExporter',
    'ExportResult',
    'ExcelConfig',
    'DataValidator',
    'ValidationChain',
    'ValidationResult',
    'create_default_validators',
    'get_template',
    'list_templates',
]

__version__ = '1.0.0'
