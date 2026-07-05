"""AKShare 数据源（默认数据源，免费开源，覆盖全）。

AKShare 文档：https://akshare.akfamily.xyz
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, List

import pandas as pd

try:
    import akshare as ak
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "使用 AKShare 数据源需要安装 akshare：pip install akshare"
    ) from e

from ...utils.exceptions import DataSourceError
from ...utils.logger import get_logger
from .base import DataSourceBase, FinancialIndicator, KLineBar, Quote, StockInfo

logger = get_logger(__name__)


# AKShare 行情列名 -> 内部字段映射
_REALTIME_COL_MAP = {
    "代码": "code",
    "名称": "name",
    "最新价": "last",
    "今开": "open",
    "最高": "high",
    "最低": "low",
    "昨收": "pre_close",
    "成交量": "volume",
    "成交额": "amount",
    "换手率": "turnover_rate",
    "市盈率-动态": "pe_ttm",
    "市净率": "pb",
    "总市值": "market_cap",
}


def _to_market(code: str) -> str:
    """根据代码判断市场。"""
    if not code or len(code) < 3:
        return ""
    if code.startswith(("60", "68", "90", "11", "13")):
        return "SH"
    if code.startswith(("00", "30", "20")):
        return "SZ"
    if code.startswith(("43", "83", "87", "88")):
        return "BJ"
    return ""


def _strip_code(code: str) -> str:
    """统一处理 600519 / sh600519 / 600519.SH -> 600519。"""
    if not code:
        return code
    s = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if s.startswith(p):
            s = s[len(p):]
            break
    return s.split(".")[0]


class AkshareSource(DataSourceBase):
    name = "akshare"

    # ---------- 股票列表 ----------
    def list_stocks(self) -> List[StockInfo]:
        try:
            df = ak.stock_info_a_code_name()
        except Exception as e:
            raise DataSourceError(f"Akshare 拉取股票列表失败: {e}") from e
        if df is None or df.empty:
            return []
        out: List[StockInfo] = []
        for _, row in df.iterrows():
            code = str(row.get("code", "")).zfill(6)
            if not code:
                continue
            out.append(
                StockInfo(
                    code=code,
                    name=str(row.get("name", "")),
                    market=_to_market(code),
                )
            )
        return out

    # ---------- 实时行情 ----------
    def get_realtime(self, codes: Iterable[str]) -> List[Quote]:
        codes_set = {_strip_code(c) for c in codes if c}
        try:
            df = ak.stock_zh_a_spot_em()
        except Exception as e:
            raise DataSourceError(f"Akshare 拉取全市场行情失败: {e}") from e
        if df is None or df.empty:
            return []

        df = df.rename(columns=_REALTIME_COL_MAP)
        if codes_set:
            df = df[df["code"].astype(str).str.zfill(6).isin(codes_set)]
        out: List[Quote] = []
        ts = datetime.now()
        for _, row in df.iterrows():
            try:
                last = float(row.get("last") or 0)
                pre_close = float(row.get("pre_close") or 0)
                amount = float(row.get("amount") or 0)
                # AKShare amount 单位是"元"，换手率是百分比
                out.append(
                    Quote(
                        code=str(row.get("code", "")).zfill(6),
                        name=str(row.get("name", "")),
                        last=last,
                        open=float(row.get("open") or 0),
                        high=float(row.get("high") or 0),
                        low=float(row.get("low") or 0),
                        pre_close=pre_close,
                        volume=float(row.get("volume") or 0),
                        amount=amount,
                        turnover_rate=float(row.get("turnover_rate") or 0),
                        pe_ttm=float(row.get("pe_ttm") or 0),
                        pb=float(row.get("pb") or 0),
                        market_cap=float(row.get("market_cap") or 0),
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
        symbol = f"{code6}"  # AKShare 直接接受 6 位代码

        # freq -> AKShare period 参数
        period_map = {
            "D": "daily",
            "W": "weekly",
            "M": "monthly",
        }
        period = period_map.get(freq.upper(), "daily")

        # adj -> adjust 参数
        adj_map = {"qfq": "qfq", "hfq": "hfq", "none": ""}
        adjust = adj_map.get(adj, "qfq")

        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period=period,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust=adjust,
            )
        except Exception as e:
            raise DataSourceError(
                f"Akshare 拉取 {code6} 历史 K 线失败: {e}"
            ) from e

        if df is None or df.empty:
            return pd.DataFrame(
                columns=[
                    "date", "open", "high", "low", "close",
                    "volume", "amount", "adj_factor",
                ]
            )

        # AKShare 列名：日期/开盘/收盘/最高/最低/成交量/成交额/振幅/涨跌幅/涨跌额/换手率
        rename = {
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount",
        }
        df = df.rename(columns=rename)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        for c in ("open", "close", "high", "low", "volume", "amount"):
            df[c] = pd.to_numeric(df.get(c), errors="coerce")
        df["adj_factor"] = 1.0
        df = df[["date", "open", "high", "low", "close", "volume", "amount", "adj_factor"]]
        df = df.dropna(subset=["close"]).reset_index(drop=True)
        return df

    # ---------- 财务数据 ----------
    def get_financial(self, code: str, start: date, end: date) -> List[FinancialIndicator]:
        code6 = _strip_code(code)
        try:
            df = ak.stock_financial_abstract(symbol=code6)
        except Exception as e:
            raise DataSourceError(
                f"Akshare 拉取 {code6} 财务数据失败: {e}"
            ) from e
        if df is None or df.empty:
            return []
        # 不同版本列名差异较大，这里做最宽松处理
        # 期望列：报告日期 / 每股收益 / 净资产收益率 / 营业收入 / 净利润
        col_map_candidates = {
            "report_date": ["报告日期", "报告期", "日期"],
            "eps": ["基本每股收益", "每股收益"],
            "roe": ["净资产收益率", "ROE"],
            "revenue": ["营业总收入", "营业收入"],
            "net_profit": ["归属于母公司股东的净利润", "净利润"],
            "revenue_growth": ["营业总收入同比增长", "营业收入同比增长", "营收增长率"],
            "net_profit_growth": ["归母净利润同比增长", "净利润同比增长"],
        }
        out: List[FinancialIndicator] = []
        for _, row in df.iterrows():
            rd_raw = None
            for k in col_map_candidates["report_date"]:
                if k in df.columns and pd.notna(row.get(k)):
                    rd_raw = row[k]
                    break
            if rd_raw is None:
                continue
            try:
                rd = pd.to_datetime(rd_raw).date()
            except Exception:
                continue
            if not (start <= rd <= end):
                continue

            def _f(keys, default=0.0):
                for k in keys:
                    if k in df.columns and pd.notna(row.get(k)):
                        try:
                            return float(row[k])
                        except (ValueError, TypeError):
                            return default
                return default

            out.append(
                FinancialIndicator(
                    code=code6,
                    report_date=rd,
                    eps=_f(col_map_candidates["eps"]),
                    roe=_f(col_map_candidates["roe"]),
                    revenue=_f(col_map_candidates["revenue"]),
                    net_profit=_f(col_map_candidates["net_profit"]),
                    revenue_growth=_f(col_map_candidates["revenue_growth"]),
                    net_profit_growth=_f(col_map_candidates["net_profit_growth"]),
                )
            )
        return out
