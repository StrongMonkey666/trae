"""数据源适配器集合。"""
from .base import DataSourceBase, Quote, KLineBar, FinancialIndicator
from .akshare_source import AkshareSource
from .tushare_source import TushareSource
from .eastmoney_source import EastMoneySource

__all__ = [
    "DataSourceBase",
    "Quote",
    "KLineBar",
    "FinancialIndicator",
    "AkshareSource",
    "TushareSource",
    "EastMoneySource",
]
