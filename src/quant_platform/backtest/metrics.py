"""回测绩效指标计算。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

import numpy as np
import pandas as pd

from .position import Trade


@dataclass
class PerformanceMetrics:
    total_return: float = 0.0        # 总收益率
    annualized_return: float = 0.0   # 年化收益率
    win_rate: float = 0.0            # 胜率
    max_drawdown: float = 0.0        # 最大回撤（负数）
    sharpe_ratio: float = 0.0        # 夏普比率（年化）
    trade_count: int = 0             # 交易笔数
    avg_profit_pct: float = 0.0      # 平均收益率（每笔）
    avg_hold_days: float = 0.0       # 平均持仓天数
    final_equity: float = 0.0        # 最终权益

    def to_dict(self) -> dict:
        return {
            "total_return": round(self.total_return, 6),
            "annualized_return": round(self.annualized_return, 6),
            "win_rate": round(self.win_rate, 6),
            "max_drawdown": round(self.max_drawdown, 6),
            "sharpe_ratio": round(self.sharpe_ratio, 6),
            "trade_count": self.trade_count,
            "avg_profit_pct": round(self.avg_profit_pct, 6),
            "avg_hold_days": round(self.avg_hold_days, 2),
            "final_equity": round(self.final_equity, 2),
        }


def compute_metrics(
    initial_capital: float,
    equity_curve: pd.DataFrame,
    trades: List[Trade],
    risk_free_rate: float = 0.02,
) -> PerformanceMetrics:
    """计算回测绩效指标。

    equity_curve: 必须包含列 date, value
    """
    m = PerformanceMetrics()
    if equity_curve is None or equity_curve.empty:
        return m
    equity_curve = equity_curve.sort_values("date").reset_index(drop=True)
    m.final_equity = float(equity_curve["value"].iloc[-1])
    m.total_return = (m.final_equity - initial_capital) / initial_capital

    # 年化
    days = (equity_curve["date"].iloc[-1] - equity_curve["date"].iloc[0]).days
    if days > 0:
        years = days / 365.25
        m.annualized_return = (1 + m.total_return) ** (1 / years) - 1 if years > 0 else 0.0

    # 最大回撤
    values = equity_curve["value"].astype(float)
    rolling_max = values.cummax()
    drawdown = (values - rolling_max) / rolling_max
    m.max_drawdown = float(drawdown.min())  # 负数

    # 夏普比率（基于日收益率）
    daily_ret = values.pct_change().dropna()
    if len(daily_ret) > 1 and daily_ret.std() > 0:
        excess = daily_ret - risk_free_rate / 252
        m.sharpe_ratio = float(
            excess.mean() / daily_ret.std() * math.sqrt(252)
        )

    # 交易统计
    m.trade_count = len(trades)
    if trades:
        profits = [t.profit_pct for t in trades]
        m.win_rate = sum(1 for p in profits if p > 0) / len(profits)
        m.avg_profit_pct = float(np.mean(profits))
        m.avg_hold_days = float(np.mean([t.hold_days for t in trades]))

    return m
