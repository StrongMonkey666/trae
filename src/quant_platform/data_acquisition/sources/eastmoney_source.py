"""东方财富数据源（兜底源）。

东方财富的接口非常稳定，作为其他源失败时的备用通道。
主要使用 push2.eastmoney.com / push2his.eastmoney.com 系列接口。
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from typing import Iterable, List

import pandas as pd
import requests

from ...utils.exceptions import DataSourceError
from ...utils.logger import get_logger
from .akshare_source import _strip_code, _to_market
from .base import DataSourceBase, FinancialIndicator, Quote, StockInfo

logger = get_logger(__name__)


# secid 规则：1.600519 / 0.000001
def _secid(code: str) -> str:
    code6 = _strip_code(code)
    m = _to_market(code6)
    if m == "SH":
        return f"1.{code6}"
    if m == "SZ":
        return f"0.{code6}"
    if m == "BJ":
        return f"0.{code6}"
    # 兜底根据代码首位猜
    if code6.startswith(("5", "6", "7", "9")):
        return f"1.{code6}"
    return f"0.{code6}"


class EastMoneySource(DataSourceBase):
    name = "eastmoney"

    QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
    BATCH_QUOTE_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    def __init__(self, timeout: int = 15, **kwargs) -> None:
        super().__init__(timeout=timeout, **kwargs)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://quote.eastmoney.com/",
            }
        )

    # ---------- 股票列表 ----------
    def list_stocks(self) -> List[StockInfo]:
        # 使用东方财富的股票列表（覆盖深沪京）
        url = "https://80.push2.eastmoney.com/api/qt/clist/get"
        out: List[StockInfo] = []
        for market_code, market in (("m:0+t:6", "SH"), ("m:0+t:80", "SZ"), ("m:0+t:81", "BJ")):
            params = {
                "pn": 1,
                "pz": 5000,
                "po": 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f12",
                "fs": market_code,
                "fields": "f12,f14",
            }
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                data = r.json()
            except Exception as e:
                logger.warning("东方财富拉取 %s 列表失败: %s", market, e)
                continue
            for item in data.get("data", {}).get("diff", []) or []:
                code = str(item.get("f12", "")).zfill(6)
                name = str(item.get("f14", ""))
                if code:
                    out.append(StockInfo(code=code, name=name, market=market))
        return out

    # ---------- 实时行情 ----------
    def get_realtime(self, codes: Iterable[str]) -> List[Quote]:
        secids = [_secid(c) for c in codes if c]
        if not secids:
            return []
        params = {
            "secid": ",".join(secids),
            "fields": (
                "f43,f44,f45,f46,f47,f48,f57,f58,f60,f107,f162,f167,f169,"
                "f170,f171,f116,f117,f85"
            ),
            "invt": 2,
            "fltt": 2,
        }
        try:
            r = self.session.get(self.QUOTE_URL, params=params, timeout=self.timeout)
            data = r.json()
        except Exception as e:
            raise DataSourceError(f"东方财富实时行情请求失败: {e}") from e

        out: List[Quote] = []
        ts = datetime.now()
        # 字段含义：f43=最新价*100, f44=最高, f45=最低, f46=今开, f47=成交量(手),
        # f48=成交额(元), f57=代码, f58=名称, f60=昨收, f107=市场,
        # f162=f170=市盈率TTM, f167=f169=市净率, f171=换手率,
        # f116=总市值, f117=流通市值, f85=流通股本
        for row in data.get("data", {}).get("diff", []) or []:
            try:
                code = str(row.get("f57", "")).zfill(6)
                if not code:
                    continue
                out.append(
                    Quote(
                        code=code,
                        name=str(row.get("f58", "")),
                        last=(row.get("f43") or 0) / 100,
                        open=(row.get("f46") or 0) / 100,
                        high=(row.get("f44") or 0) / 100,
                        low=(row.get("f45") or 0) / 100,
                        pre_close=(row.get("f60") or 0) / 100,
                        volume=(row.get("f47") or 0) * 100,  # 手 -> 股
                        amount=float(row.get("f48") or 0),
                        turnover_rate=float(row.get("f171") or 0),
                        pe_ttm=float(row.get("f162") or row.get("f170") or 0),
                        pb=float(row.get("f167") or row.get("f169") or 0),
                        market_cap=float(row.get("f116") or 0) * 1e8,  # 亿 -> 元
                        timestamp=ts,
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
        sid = _secid(code6)

        # klt: 101=日K 102=周K 103=月K 1/5/15/30/60=分钟
        klt_map = {"D": 101, "W": 102, "M": 103, "1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60}
        klt = klt_map.get(freq, 101)

        # fqt: 0=不复权 1=前复权 2=后复权
        fqt_map = {"none": 0, "qfq": 1, "hfq": 2}
        fqt = fqt_map.get(adj, 1)

        rows: list[str] = []
        beg = start.strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")
        for offset in range(0, 800, 500):  # 分页拉取
            params = {
                "secid": sid,
                "klt": klt,
                "fqt": fqt,
                "beg": beg,
                "end": end_s,
                "lmt": 500,
                "end": end_s,
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "mkltt": 0,
                "mktype": 0,
            }
            try:
                r = self.session.get(self.KLINE_URL, params=params, timeout=self.timeout)
                data = r.json()
            except Exception as e:
                raise DataSourceError(
                    f"东方财富拉取 {code6} K 线失败: {e}"
                ) from e
            kl = (data.get("data") or {}).get("klines") or []
            rows.extend(kl)
            if len(kl) < 500:
                break
            # 推进 beg 防止重复
            if rows:
                last_date = rows[-1].split(",")[0]
                beg = last_date
            time.sleep(0.1)

        if not rows:
            return pd.DataFrame(
                columns=[
                    "date", "open", "high", "low", "close",
                    "volume", "amount", "adj_factor",
                ]
            )

        records = []
        for line in rows:
            parts = line.split(",")
            if len(parts) < 6:
                continue
            try:
                d = datetime.strptime(parts[0], "%Y-%m-%d").date()
                rec = {
                    "date": d,
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]) * 100,  # 手 -> 股
                    "amount": float(parts[6]) if len(parts) > 6 else 0.0,
                    "adj_factor": 1.0,
                }
                records.append(rec)
            except (ValueError, IndexError):
                continue
        df = pd.DataFrame(records)
        df = df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)
        return df

    # ---------- 财务数据 ----------
    def get_financial(self, code: str, start: date, end: date) -> List[FinancialIndicator]:
        # 东方财富没有公开稳定的财务接口，财务建议走 AKShare / Tushare
        logger.warning("东方财富数据源未实现财务数据接口，请使用 AKShare 或 Tushare")
        return []
