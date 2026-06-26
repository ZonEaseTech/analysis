"""bq_reports 报表层共享基础设施 — 报表引擎 + 文件缓存。

原在 utils/(误标为"通用工具")，实际只被 bq_reports 层消费。
搬到这里后 semantic/ 不再反向依赖 bq_reports/。
"""
from bq_reports.shared.report_engine import ReportEngine, load_sheet_config, ColumnConfig, SheetConfig, query_all_shops
from bq_reports.shared.cache import get_cache, set_cache, cache_key, cached
