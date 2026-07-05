"""统一数据服务层（UnifiedDataService）。

对外暴露：
    - get_history_data(code, start, end, freq, adj)   历史 K 线
    - get_realtime_data(codes)                        实时行情
    - get_financial_data(code, start, end)            财务指标
    - get_stock_list()                                股票列表
    - sync_history(...)                               触发一次同步
    - sync_stock_list()                               同步股票列表
    - sync_realtime(...)                              同步实时行情
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from ..data_acquisition.cleaner import clean_history
from ..data_acquisition.sources import (
    AkshareSource,
    DataSourceBase,
    EastMoneySource,
    TushareSource,
)
from ..utils.config import deep_get, load_config
from ..utils.exceptions import DataSourceNotEnabled, QuantPlatformError
from ..utils.logger import get_logger
from .storage import DataStore

logger = get_logger(__name__)


def build_sources(config: Dict[str, Any]) -> Dict[str, DataSourceBase]:
    """根据配置构造启用的数据源。"""
    sources: Dict[str, DataSourceBase] = {}
    cfg = config.get("data_sources", {})

    if cfg.get("akshare", {}).get("enabled", True):
        sources["akshare"] = AkshareSource(
            timeout=cfg.get("akshare", {}).get("timeout", 15)
        )

    if cfg.get("tushare", {}).get("enabled", False):
        token = cfg.get("tushare", {}).get("token", "")
        if token:
            try:
                sources["tushare"] = TushareSource(
                    token=token,
                    timeout=cfg.get("tushare", {}).get("timeout", 15),
                )
            except DataSourceNotEnabled as e:
                logger.warning("Tushare 初始化失败: %s", e)
        else:
            logger.info("Tushare 未配置 token，已跳过")

    if cfg.get("eastmoney", {}).get("enabled", True):
        sources["eastmoney"] = EastMoneySource(
            timeout=cfg.get("eastmoney", {}).get("timeout", 15)
        )

    return sources


class UnifiedDataService:
    """统一数据服务：根据优先级降级调用数据源，结果落盘到 DataStore。"""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        store: Optional[DataStore] = None,
    ) -> None:
        self.config = config or load_config()
        self.store = store or DataStore(
            sqlite_path=deep_get(self.config, "data_service", "storage", "sqlite_path",
                                 default="data/sqlite/quant.db"),
            hdf5_path=deep_get(self.config, "data_service", "storage", "hdf5_path",
                               default="data/hdf5/market.h5"),
        )
        self.sources: Dict[str, DataSourceBase] = build_sources(self.config)
        self.priority: List[str] = deep_get(
            self.config, "data_service", "source_priority",
            default=list(self.sources.keys()),
        )
        self.realtime_source: str = deep_get(
            self.config, "data_service", "realtime_source",
            default=self.priority[0] if self.priority else "akshare",
        )
        logger.info(
            "UnifiedDataService 初始化完成, sources=%s, priority=%s",
            list(self.sources.keys()), self.priority,
        )

    # ============================================================
    # 对外接口
    # ============================================================
    def get_history_data(
        self,
        code: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
        freq: str = "D",
        adj: str = "qfq",
        auto_sync: bool = True,
    ) -> pd.DataFrame:
        """获取历史 K 线。本地优先；缺失或不足时自动回源补齐。"""
        code6 = str(code).zfill(6)
        end = end or date.today()
        start = start or (end - timedelta(days=365 * deep_get(
            self.config, "data_service", "cache", "history_years", default=10
        )))

        df = self.store.hdf5.load_kline(code6)
        need_sync = df.empty
        latest = None
        if not df.empty:
            df_dates = pd.to_datetime(df["date"]).dt.date
            latest = df_dates.max() if not df_dates.empty else None
            if latest is None or latest < end:
                need_sync = True

        if need_sync and auto_sync:
            sync_start = start
            if latest is not None and latest >= start:
                sync_start = latest
            self.sync_history(code6, start=sync_start, end=end, freq=freq)
            df = self.store.hdf5.load_kline(code6)

        if df.empty:
            return df
        df = df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)
        # 注意：HDF5 中保存的已是 qfq 复权
        if adj == "none":
            df = df.copy()
            for c in ("open", "high", "low", "close"):
                if "adj_factor" in df.columns:
                    df[c] = df[c] / df["adj_factor"]
        return df

    def get_realtime_data(self, codes: Optional[Iterable[str]] = None) -> pd.DataFrame:
        """获取实时行情。无网络时不读缓存，直接返回空。"""
        source = self.sources.get(self.realtime_source)
        if source is None:
            raise QuantPlatformError(f"未配置可用实时数据源: {self.realtime_source}")
        codes_list = [str(c).zfill(6) for c in codes] if codes else None
        try:
            if codes_list is not None:
                quotes = source.get_realtime(codes_list)
            else:
                # 如果数据源支持一次拉全市场，尝试取股票列表再批量
                if hasattr(source, "get_realtime") and not hasattr(source, "list_stocks"):
                    quotes = source.get_realtime(codes_list or [])
                else:
                    stocks = source.list_stocks()
                    quotes = source.get_realtime([s.code for s in stocks])
        except Exception as e:
            self.store.sqlite.update_source_status(source.name, ok=False, note=str(e))
            logger.warning("实时数据源 %s 失败: %s", source.name, e)
            quotes = []
        if not quotes:
            return pd.DataFrame()
        self.store.sqlite.update_source_status(source.name, ok=True, note="ok")
        return pd.DataFrame([q.__dict__ for q in quotes])

    def get_financial_data(
        self,
        code: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> pd.DataFrame:
        code6 = str(code).zfill(6)
        df = self.store.hdf5.load_financial(code6)
        if df.empty or auto_need_sync_financial(df, end or date.today()):
            self.sync_financial(code6, start=start, end=end)
            df = self.store.hdf5.load_financial(code6)
        if df.empty:
            return df
        if start:
            df = df[df["report_date"] >= start]
        if end:
            df = df[df["report_date"] <= end]
        return df.reset_index(drop=True)

    def get_stock_list(self, market: Optional[str] = None) -> pd.DataFrame:
        df = self.store.sqlite.list_stocks(market=market)
        if df.empty:
            self.sync_stock_list()
            df = self.store.sqlite.list_stocks(market=market)
        return df

    # ============================================================
    # 同步任务
    # ============================================================
    def sync_stock_list(self) -> int:
        """同步全市场股票列表。"""
        n_total = 0
        for name in self.priority:
            src = self.sources.get(name)
            if src is None:
                continue
            try:
                stocks = src.list_stocks()
                rows = [
                    {
                        "code": s.code,
                        "name": s.name,
                        "market": s.market,
                        "industry": s.industry,
                        "list_date": s.list_date,
                    }
                    for s in stocks
                ]
                n = self.store.sqlite.upsert_stocks(rows)
                self.store.sqlite.update_source_status(name, ok=True, note=f"upserted {n}")
                logger.info("[%s] 同步股票列表 %d 条", name, n)
                n_total = max(n_total, n)
                return n
            except Exception as e:
                self.store.sqlite.update_source_status(name, ok=False, note=str(e))
                logger.warning("[%s] 同步股票列表失败: %s", name, e)
        return n_total

    def sync_history(
        self,
        code: str,
        start: date,
        end: date,
        freq: str = "D",
    ) -> int:
        code6 = str(code).zfill(6)
        rows = 0
        for name in self.priority:
            src = self.sources.get(name)
            if src is None:
                continue
            try:
                df = src.get_history(code6, start=start, end=end, freq=freq, adj="qfq")
                df = clean_history(df, adj="qfq")
                self.store.hdf5.save_kline(code6, df)
                rows = len(df)
                self.store.sqlite.log_sync(
                    source=name, freq=freq,
                    start_date=start, end_date=end,
                    rows=rows, ok=True, code=code6,
                )
                self.store.sqlite.update_source_status(name, ok=True, note="ok")
                logger.info("[%s] 同步 %s K 线 %d 条", name, code6, rows)
                return rows
            except Exception as e:
                self.store.sqlite.log_sync(
                    source=name, freq=freq,
                    start_date=start, end_date=end,
                    rows=0, ok=False, code=code6, error=str(e),
                )
                self.store.sqlite.update_source_status(name, ok=False, note=str(e))
                logger.warning("[%s] 同步 %s K 线失败: %s", name, code6, e)
        return rows

    def sync_realtime(self, codes: Optional[Iterable[str]] = None) -> int:
        """拉取实时行情，缓存到内存表（不落 HDF5，历史 K 线才落 HDF5）。"""
        df = self.get_realtime_data(codes=codes)
        return len(df)

    def sync_financial(
        self,
        code: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> int:
        code6 = str(code).zfill(6)
        end = end or date.today()
        start = start or (end - timedelta(days=365 * 5))
        # 财务数据优先 Tushare（结构化稳定），其次 AKShare
        for name in (["tushare", "akshare"] if "tushare" in self.sources else ["akshare"]):
            src = self.sources.get(name)
            if src is None:
                continue
            try:
                items = src.get_financial(code6, start=start, end=end)
                if not items:
                    continue
                df = pd.DataFrame([i.__dict__ for i in items])
                # report_date -> date
                if "report_date" in df.columns:
                    df = df.rename(columns={"report_date": "report_date"})
                self.store.hdf5.save_financial(code6, df)
                logger.info("[%s] 同步 %s 财务数据 %d 条", name, code6, len(df))
                return len(df)
            except Exception as e:
                logger.warning("[%s] 同步 %s 财务失败: %s", name, code6, e)
        return 0


def auto_need_sync_financial(df: pd.DataFrame, today: date) -> bool:
    """判断财务数据是否需要重新同步（季度更新）。"""
    if df.empty:
        return True
    last = pd.to_datetime(df["report_date"]).dt.date.max()
    if last is None:
        return True
    # 距离最近一次季报超过 100 天则触发
    return (today - last).days > 100
