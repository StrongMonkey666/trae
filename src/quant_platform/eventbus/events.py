"""事件类型定义。

所有事件均使用 dataclass，自动 to_dict() 用于序列化。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


# ============================================================
# 事件分类常量
# ============================================================
CAT_BACKTEST = "backtest"
CAT_SELECTOR = "selector"
CAT_TRADE = "trade"
CAT_PORTFOLIO = "portfolio"
CAT_SYSTEM = "system"


@dataclass
class Event:
    """事件基类。"""

    event_type: str            # 例如 "backtest.completed"
    category: str = CAT_SYSTEM
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    source: str = ""           # 事件源（模块名）
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 预置事件类型（用于 IDE 自动补全 + 类型校验）
# ============================================================
@dataclass
class BacktestCompletedEvent(Event):
    event_type: str = "backtest.completed"
    category: str = CAT_BACKTEST
    record_id: int = 0
    name: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d.update({"record_id": self.record_id, "name": self.name, "metrics": self.metrics})
        return d


@dataclass
class SelectorCompletedEvent(Event):
    event_type: str = "selector.completed"
    category: str = CAT_SELECTOR
    record_id: int = 0
    hit_count: int = 0
    natural_lang: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d.update({
            "record_id": self.record_id,
            "hit_count": self.hit_count,
            "natural_lang": self.natural_lang,
        })
        return d


@dataclass
class TradeEvent(Event):
    """单次成交事件（模拟 / 回测共用）。"""

    event_type: str = "trade.executed"
    category: str = CAT_TRADE
    instance_id: int = 0
    code: str = ""
    name: str = ""
    side: str = ""           # buy / sell
    price: float = 0.0
    shares: int = 0
    amount: float = 0.0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d.update({
            "instance_id": self.instance_id,
            "code": self.code, "name": self.name,
            "side": self.side, "price": self.price,
            "shares": self.shares, "amount": self.amount,
            "reason": self.reason,
        })
        return d


@dataclass
class SignalEvent(Event):
    """策略信号事件：止盈 / 止损 / 持股到期 / 条件不符。"""

    event_type: str = "signal.triggered"
    category: str = CAT_PORTFOLIO
    instance_id: int = 0
    code: str = ""
    name: str = ""
    signal: str = ""         # stop_loss / take_profit / max_holding_days / condition_fail
    profit_pct: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d.update({
            "instance_id": self.instance_id,
            "code": self.code, "name": self.name,
            "signal": self.signal, "profit_pct": self.profit_pct,
            "extra": self.extra,
        })
        return d


@dataclass
class DeployedEvent(Event):
    event_type: str = "simulator.deployed"
    category: str = CAT_PORTFOLIO
    instance_id: int = 0
    backtest_id: int = 0
    initial_capital: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d.update({
            "instance_id": self.instance_id,
            "backtest_id": self.backtest_id,
            "initial_capital": self.initial_capital,
        })
        return d
