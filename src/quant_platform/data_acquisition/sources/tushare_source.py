"""Tushare Pro 数据源（可选，需要 token，积分制）。

文档：https://tushare.pro/document/2
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, List

import pandas as pd

try:
    import tushare as ts
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "使用 Tushare 数据源需要安装 tushare：pip install tushare"
    ) from e

from ...utils.exceptions import DataSourceError, DataSourceNotEnabled
from ...utils.logger import get_logger
from .akshare_source import _strip_code, _to_market
from .base import DataSourceBase, FinancialIndicator, Quote, StockInfo

logger = get_logger(__name__)


class TushareSource(DataSourceBase):
    name = "tushare"

    def __init__(self, token: str = "", timeout: int = 15, **kwargs) -> None:
        super().__init__(timeout=timeout, **kwargs)
        self.token = token
        if not token:
            raise DataSourceNotEnabled("Tushare 未配置 token")
        ts.set_token(token)
        self.pro = ts.pro_api()

    # ---------- 股票列表 ----------
    def list_stocks(self) -> List[StockInfo]:
        try:
            df = self.pro.stock_basic(
                list_status="L",
                fields="ts_code,symbol,name,industry,list_date,exchange",
            )
        except Exception as e:
            raise DataSourceError(f"Tushare 拉取股票列表失败: {e}") from e
        out: List[StockInfo] = []
        for _, row in df.iterrows():
            code = str(row.get("symbol", "")).zfill(6)
            exchange = str(row.get("exchange", ""))
            market = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}.get(exchange, "")
            ld = row.get("list_date")
            try:
                list_date = pd.to_datetime(ld).date() if pd.notna(ld) else None
            except Exception:
                list_date = None
            out.append(
                StockInfo(
                    code=code,
                    name=str(row.get("name", "")),
                    market=market or _to_market(code),
                    industry=str(row.get("industry", "") or ""),
                    list_date=list_date,
                )
            )
        return out

    # ---------- 实时行情 ----------
    def get_realtime(self, codes: Iterable[str]) -> List[Quote]:
        codes_list = [_strip_code(c) for c in codes if c]
        if not codes_list:
            return []
        ts_codes = []
        for c in codes_list:
            m = _to_market(c)
            ts_codes.append(f"{c}.{m}" if m else c)
        try:
            df = self.pro.quote(ts_codes=ts_codes)
        except Exception as e:
            raise DataSourceError(f"Tushare 拉取实时行情失败: {e}") from e
        out: List[Quote] = []
        ts_now = datetime.now()
        for _, row in df.iterrows():
            try:
                out.append(
                    Quote(
                        code=str(row.get("ts_code", "")).split(".")[0].zfill(6),
                        name=str(row.get("name", "")),
                        last=float(row.get("last_close") or 0),
                        open=float(row.get("open") or 0),
                        high=float(row.get("high") or 0),
                        low=float(row.get("low") or 0),
                        pre_close=float(row.get("pre_close") or 0),
                        volume=float(row.get("vol") or 0),
                        amount=float(row.get("amount") or 0),
                        turnover_rate=float(row.get("turnover_rate") or 0),
                        pe_ttm=float(row.get("pe_ttm") or 0),
                        pb=float(row.get("pb") or 0),
                        market_cap=float(row.get("total_mv") or 0) * 1e4,  # 万 -> 元
                        timestamp=ts_now,
                        source=self.name,
                    )
                )
            except (ValueError, TypeError):
                continue
        return out

    # ---------- 历史 K 线 ----------
    def get_history(
        self,
        code: str,
        start: date,
        end: date,
        freq: str = "D",
        adj: str = "qfq",
    ) -> pd.DataFrame:
        code6 = _strip_code(code)
        m = _to_market(code6)
        ts_code = f"{code6}.{m}" if m else code6

        # Tushare 日/周/月
        freq_map = {"D": "D", "W": "W", "M": "M"}
        t_freq = freq_map.get(freq.upper(), "D")

        # Tushare 复权
        adj_map = {"qfq": "qfq", "hfq": "hfq", "none": None}
        t_adj = adj_map.get(adj, "qfq")

        try:
            df = ts.pro_bar(
                ts_code=ts_code,
                freq=t_freq,
                adj=t_adj,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
        except Exception as e:
            raise DataSourceError(
                f"Tushare 拉取 {ts_code} 历史 K 线失败: {e}"
            ) from e
        if df is None or df.empty:
            return pd.DataFrame(
                columns=[
                    "date", "open", "high", "low", "close",
                    "volume", "amount", "adj_factor",
                ]
            )
        df = df.rename(
            columns={
                "trade_date": "date", "vol": "volume",
            }
        )
        df["date"] = pd.to_datetime(df["date"]).dt.date
        for c in ("open", "high", "low", "close", "volume", "amount"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "adj_factor" not in df.columns:
            df["adj_factor"] = 1.0
        df = df[["date", "open", "high", "low", "close", "volume", "amount", "adj_factor"]]
        df = df.dropna(subset=["close"]).reset_index(drop=True)
        return df

    # ---------- 财务数据 ----------
    def get_financial(self, code: str, start: date, end: date) -> List[FinancialIndicator]:
        code6 = _strip_code(code)
        m = _to_market(code6)
        ts_code = f"{code6}.{m}" if m else code6
        try:
            df = self.pro.fina_indicator(
                ts_code=ts_code,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
        except Exception as e:
            raise DataSourceError(
                f"Tushare 拉取 {ts_code} 财务指标失败: {e}"
            ) from e
        if df is None or df.empty:
            return []
        out: List[FinancialIndicator] = []
        for _, row in df.iterrows():
            try:
                rd = pd.to_datetime(row["end_date"]).date()
            except Exception:
                continue
            if not (start <= rd <= end):
                continue
            out.append(
                FinancialIndicator(
                    code=code6,
                    report_date=rd,
                    eps=float(row.get("eps") or 0),
                    roe=float(row.get("roe") or 0),
                    revenue=float(row.get("revenue") or 0),
                    net_profit=float(row.get("netprofit") or 0),
                    revenue_growth=float(row.get("or_yoy") or 0),
                    net_profit_growth=float(row.get("np_yoy") or 0),
                    gross_margin=float(row.get("grossprofit_margin") or 0),
                    debt_ratio=float(row.get("debt_to_assets") or 0),
                )
            )
        return out
