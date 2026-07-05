"""持仓管理：单只股票的持仓状态。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Position:
    code: str
    name: str = ""
    shares: int = 0
    avg_cost: float = 0.0      # 含费成本
    buy_date: Optional[date] = None
    # 止盈观察区
    peak_price: float = 0.0    # 持仓期间最高收盘价
    in_tp_zone: bool = False   # 是否已进入止盈观察区
    # 最近一次卖出原因（用于回测日志）
    last_signal: str = ""

    @property
    def market_value(self) -> float:
        return 0.0  # 由外部以 close 注入

    def update_take_profit_zone(self, high: float) -> None:
        if high > self.peak_price:
            self.peak_price = high
        # 一旦达到过止盈阈值，标记为已进入观察区
        if not self.in_tp_zone and self.peak_price > 0 and self.avg_cost > 0:
            if (self.peak_price - self.avg_cost) / self.avg_cost >= 0:  # 占位
                pass

    def profit_pct(self, close: float) -> float:
        if self.avg_cost <= 0:
            return 0.0
        return (close - self.avg_cost) / self.avg_cost


@dataclass
class Trade:
    """一次完整买入-卖出记录。"""

    code: str
    name: str = ""
    buy_date: Optional[date] = None
    buy_price: float = 0.0
    sell_date: Optional[date] = None
    sell_price: float = 0.0
    shares: int = 0
    profit_pct: float = 0.0       # 收益率（含费）
    profit_amount: float = 0.0    # 盈亏金额
    hold_days: int = 0
    sell_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "buy_date": self.buy_date.isoformat() if self.buy_date else None,
            "buy_price": round(self.buy_price, 4),
            "sell_date": self.sell_date.isoformat() if self.sell_date else None,
            "sell_price": round(self.sell_price, 4),
            "shares": self.shares,
            "profit_pct": round(self.profit_pct, 6),
            "profit_amount": round(self.profit_amount, 2),
            "hold_days": self.hold_days,
            "sell_reason": self.sell_reason,
        }
