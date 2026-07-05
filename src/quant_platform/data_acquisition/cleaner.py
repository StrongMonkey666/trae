"""数据清洗模块。

负责：
- 缺失值处理
- 复权计算（基于 adj_factor）
- 停牌标记
- 字段标准化
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

REQUIRED_COLS = ["date", "open", "high", "low", "close", "volume", "amount", "adj_factor"]


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """统一列名与小写化，补齐缺失列。"""
    if df is None or df.empty:
        return pd.DataFrame(columns=REQUIRED_COLS)
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    for c in REQUIRED_COLS:
        if c not in df.columns:
            df[c] = np.nan
    return df[REQUIRED_COLS]


def fill_missing(df: pd.DataFrame) -> pd.DataFrame:
    """对缺失值做最小填充：OHLC 用前值，volume/amount 用 0。

    不做复杂的插值，避免引入未来信息。停牌日的 volume/amount 保持 0。
    """
    if df is None or df.empty:
        return df
    df = df.copy()
    for c in ("open", "high", "low", "close", "adj_factor"):
        df[c] = df[c].ffill()
    for c in ("volume", "amount"):
        df[c] = df[c].fillna(0.0)
    return df


def mark_suspended(df: pd.DataFrame) -> pd.DataFrame:
    """标记停牌日：volume == 0 或 close 与前值相同。"""
    if df is None or df.empty:
        return df
    df = df.copy()
    if "suspended" not in df.columns:
        df["suspended"] = False
    zero_vol = (df["volume"] == 0) & (df["amount"] == 0)
    flat = (df["close"] == df["close"].shift(1)) & (df["volume"] == 0)
    df["suspended"] = (zero_vol | flat).fillna(False).astype(bool)
    return df


def apply_adjustment(df: pd.DataFrame, adj: str = "qfq") -> pd.DataFrame:
    """根据 adj_factor 调整为指定复权方式。

    adj: 'qfq' 前复权 / 'hfq' 后复权 / 'none' 不复权
    假设 adj_factor 已是后复权因子（与 Tushare/AKShare qfq 输出一致）。
    """
    if df is None or df.empty or "adj_factor" not in df.columns:
        return df
    df = df.copy()
    if adj == "none":
        return df.drop(columns=["adj_factor"])
    if adj == "hfq":
        for c in ("open", "high", "low", "close"):
            df[c] = df[c] * df["adj_factor"]
        return df.drop(columns=["adj_factor"])
    # qfq: 假定数据已是 qfq，保留原状
    return df.drop(columns=["adj_factor"])


def clean_history(
    df: pd.DataFrame,
    adj: str = "qfq",
    drop_duplicates: bool = True,
) -> pd.DataFrame:
    """一站式清洗入口。"""
    df = normalize_columns(df)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    if drop_duplicates:
        df = df.drop_duplicates(subset=["date"], keep="last")
    df = df.sort_values("date").reset_index(drop=True)
    df = fill_missing(df)
    df = mark_suspended(df)
    df = apply_adjustment(df, adj=adj)
    return df


def merge_incremental(
    existing: Optional[pd.DataFrame],
    new: pd.DataFrame,
) -> pd.DataFrame:
    """合并增量：保留 existing 中已有日期，更新/追加 new 中的数据。"""
    new = clean_history(new)
    if existing is None or existing.empty:
        return new
    ex = clean_history(existing)
    # existing 保留 adj_factor 列以便复用
    if "adj_factor" not in ex.columns:
        ex["adj_factor"] = 1.0
    base = ex.set_index("date")
    upd = new.set_index("date")
    if not upd.empty:
        base.update(upd)
        # 追加新日期
        for d, row in upd.iterrows():
            if d not in base.index:
                base.loc[d] = row
    base = base.sort_index().reset_index()
    return base
