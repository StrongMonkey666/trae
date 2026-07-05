"""数据源抽象基类。

所有外部数据源（AKShare/Tushare/东方财富/通达信等）都必须实现该接口。
新增数据源时只需继承 DataSourceBase 并实现以下方法。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, List, Optional

import pandas as pd


@dataclass
class StockInfo:
    """股票基础信息。"""

    code: str               # 6 位代码（不含市场前缀），如 "600519"
    name: str
    market: str = "SH"      # SH / SZ / BJ
    industry: str = ""
    list_date: Optional[date] = None


@dataclass
class Quote:
    """实时行情快照。"""

    code: str
    name: str = ""
    last: float = 0.0          # 最新价
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    pre_close: float = 0.0
    volume: float = 0.0
    amount: float = 0.0
    turnover_rate: float = 0.0
    pe_ttm: float = 0.0
    pb: float = 0.0
    market_cap: float = 0.0     # 总市值（元）
    timestamp: Optional[datetime] = None
    source: str = ""


@dataclass
class KLineBar:
    """单根 K 线。"""

    code: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float = 0.0
    freq: str = "D"             # D / W / M / 1m / 5m / 15m / 30m / 60m
    adj_factor: float = 1.0     # 复权因子
    suspended: bool = False


@dataclass
class FinancialIndicator:
    """财务/资金面指标。"""

    code: str
    report_date: date
    eps: float = 0.0
    roe: float = 0.0
    revenue: float = 0.0
    net_profit: float = 0.0
    revenue_growth: float = 0.0
    net_profit_growth: float = 0.0
    gross_margin: float = 0.0
    debt_ratio: float = 0.0
    extra: dict = field(default_factory=dict)


class DataSourceBase(abc.ABC):
    """数据源统一接口。

    所有数据源都必须支持以下能力：
    - 获取股票列表
    - 拉取实时行情
    - 拉取历史 K 线
    - 拉取财务指标
    """

    name: str = "base"

    def __init__(self, timeout: int = 15, **kwargs) -> None:
        self.timeout = timeout
        self._kwargs = kwargs

    # ---------- 股票列表 ----------
    @abc.abstractmethod
    def list_stocks(self) -> List[StockInfo]:
        """获取全市场股票列表。"""

    # ---------- 实时行情 ----------
    @abc.abstractmethod
    def get_realtime(self, codes: Iterable[str]) -> List[Quote]:
        """获取指定代码的实时行情。"""

    # ---------- 历史 K 线 ----------
    @abc.abstractmethod
    def get_history(
        self,
        code: str,
        start: date,
        end: date,
        freq: str = "D",
        adj: str = "qfq",
    ) -> pd.DataFrame:
        """拉取历史 K 线。

        返回 DataFrame 必须至少包含列：
            date, open, high, low, close, volume, amount, adj_factor
        """

    # ---------- 财务数据 ----------
    @abc.abstractmethod
    def get_financial(self, code: str, start: date, end: date) -> List[FinancialIndicator]:
        """拉取财务指标。"""

    # ---------- 健康检查 ----------
    def ping(self) -> bool:
        """简单心跳检查，默认拉一次股票列表。"""
        try:
            stocks = self.list_stocks()
            return len(stocks) > 0
        except Exception:
            return False
