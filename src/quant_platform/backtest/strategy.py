"""策略配置：从回测记录系统序列化/反序列化。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, Optional

from ..selector.schema import SelectorSpec
from ..utils.exceptions import QuantPlatformError


VALID_FREQ = ("daily", "weekly", "monthly")
VALID_CAPITAL_MODEL = ("equal_weight", "score_weight", "fixed_amount", "kelly")


@dataclass
class StrategyConfig:
    """回测/持仓策略完整参数。"""

    name: str = "default"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    initial_capital: float = 1_000_000.0

    # 调仓
    rebalance_freq: str = "weekly"   # daily/weekly/monthly

    # 选股
    selector: Optional[SelectorSpec] = None

    # 持仓限制
    max_holdings: int = 5
    max_buy_per_day: int = 2
    max_sell_per_day: int = 3

    # 止盈/止损（固定规则）
    take_profit_threshold: float = 0.20   # 进入观察区的收益阈值
    take_profit_drawdown: float = 0.05    # 观察区回落幅度
    stop_loss: float = -0.08              # 硬止损

    # 其他卖出条件
    max_holding_days: int = 30

    # 资金分配
    capital_model: str = "equal_weight"
    fixed_amount: float = 10_000.0        # 固定金额模式的单股金额
    kelly_fraction: float = 0.5           # 凯利公式系数

    # 交易成本
    fee_rate: float = 0.0003      # 佣金（按交易金额双边收取）
    stamp_tax: float = 0.001      # 印花税（仅卖出）
    slippage: float = 0.001       # 滑点

    # 可选：技术卖出条件（dict，简化为字段+operator+value）
    technical_sell: Optional[Dict[str, Any]] = None

    # 可选：自然语言原文（仅记录）
    natural_language: str = ""

    def validate(self) -> None:
        if self.start_date is None or self.end_date is None:
            raise QuantPlatformError("start_date / end_date 必填")
        if self.start_date >= self.end_date:
            raise QuantPlatformError("start_date 必须早于 end_date")
        if self.rebalance_freq not in VALID_FREQ:
            raise QuantPlatformError(f"rebalance_freq 非法: {self.rebalance_freq}")
        if self.capital_model not in VALID_CAPITAL_MODEL:
            raise QuantPlatformError(f"capital_model 非法: {self.capital_model}")
        if self.max_holdings <= 0:
            raise QuantPlatformError("max_holdings 必须 > 0")
        if self.initial_capital <= 0:
            raise QuantPlatformError("initial_capital 必须 > 0")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["start_date"] = self.start_date.isoformat() if self.start_date else None
        d["end_date"] = self.end_date.isoformat() if self.end_date else None
        d["selector"] = self.selector.to_dict() if self.selector else None
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StrategyConfig":
        d = dict(d)
        d["start_date"] = date.fromisoformat(d["start_date"]) if d.get("start_date") else None
        d["end_date"] = date.fromisoformat(d["end_date"]) if d.get("end_date") else None
        if d.get("selector"):
            d["selector"] = SelectorSpec.from_dict(d["selector"])
        return cls(**d)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, s: str) -> "StrategyConfig":
        return cls.from_dict(json.loads(s))
