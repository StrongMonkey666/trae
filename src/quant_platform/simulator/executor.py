"""模拟交易执行器（撮合）。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from ..backtest.position import Position
from ..utils.logger import get_logger
from .state import SimState

logger = get_logger(__name__)


FEE_RATE = 0.0003
STAMP_TAX = 0.001


@dataclass
class ExecResult:
    code: str
    side: str            # buy / sell
    price: float
    shares: int
    amount: float        # 成交金额（不含费）
    fee: float
    tax: float
    success: bool
    reason: str = ""


class PaperExecutor:
    """模拟撮合：收到订单后立即以指定价格成交（无对手方，无滑点）。"""

    def __init__(self, state: SimState, instance_id: int) -> None:
        self.state = state
        self.instance_id = instance_id

    def buy(
        self,
        code: str,
        name: str,
        price: float,
        amount: float,
        reason: str = "",
    ) -> ExecResult:
        if price <= 0 or amount <= 0:
            return ExecResult(
                code=code, side="buy", price=price, shares=0,
                amount=0, fee=0, tax=0, success=False,
                reason="invalid_price_or_amount",
            )
        # 100 股一手
        shares = int(amount // (price * 100)) * 100
        if shares <= 0:
            return ExecResult(
                code=code, side="buy", price=price, shares=0,
                amount=0, fee=0, tax=0, success=False,
                reason="insufficient_for_one_lot",
            )
        cost = shares * price
        fee = max(cost * FEE_RATE, 5.0)
        total_cost = cost + fee
        cash = self.state.get_cash(self.instance_id)
        if total_cost > cash:
            # 缩减股数
            affordable = int(
                (cash / (price * (1 + FEE_RATE))) // 100
            ) * 100
            if affordable <= 0:
                return ExecResult(
                    code=code, side="buy", price=price, shares=0,
                    amount=0, fee=0, tax=0, success=False,
                    reason="insufficient_cash",
                )
            shares = affordable
            cost = shares * price
            fee = max(cost * FEE_RATE, 5.0)
            total_cost = cost + fee
        # 扣现金
        self.state.set_cash(self.instance_id, cash - total_cost)
        # 更新持仓（合并：若有同 code，按加权成本）
        positions = {p.code: p for p in self.state.get_positions(self.instance_id)}
        avg_cost = total_cost / shares
        if code in positions:
            old = positions[code]
            total_shares = old.shares + shares
            new_cost = (old.shares * old.avg_cost + shares * avg_cost) / total_shares
            old.shares = total_shares
            old.avg_cost = new_cost
            self.state.upsert_position(self.instance_id, old)
        else:
            self.state.upsert_position(self.instance_id, Position(
                code=code, name=name, shares=shares,
                avg_cost=avg_cost, buy_date=date.today(),
                peak_price=price,
            ))
        # 写成交
        self.state.add_trade(
            self.instance_id, code, name, "buy", price, shares,
            cost, fee=fee, tax=0.0, reason=reason,
        )
        return ExecResult(
            code=code, side="buy", price=price, shares=shares,
            amount=cost, fee=fee, tax=0.0, success=True, reason=reason,
        )

    def sell(
        self,
        code: str,
        name: str,
        price: float,
        shares: Optional[int] = None,
        reason: str = "",
    ) -> ExecResult:
        positions = {p.code: p for p in self.state.get_positions(self.instance_id)}
        pos = positions.get(code)
        if pos is None:
            return ExecResult(
                code=code, side="sell", price=price, shares=0,
                amount=0, fee=0, tax=0, success=False,
                reason="no_position",
            )
        sell_shares = shares if shares and shares > 0 else pos.shares
        sell_shares = min(sell_shares, pos.shares)
        if sell_shares <= 0:
            return ExecResult(
                code=code, side="sell", price=price, shares=0,
                amount=0, fee=0, tax=0, success=False,
                reason="zero_shares",
            )
        proceeds = sell_shares * price
        fee = max(proceeds * FEE_RATE, 5.0)
        tax = proceeds * STAMP_TAX
        net = proceeds - fee - tax
        cash = self.state.get_cash(self.instance_id)
        self.state.set_cash(self.instance_id, cash + net)
        # 减仓
        pos.shares -= sell_shares
        if pos.shares <= 0:
            self.state.delete_position(self.instance_id, code)
        else:
            self.state.upsert_position(self.instance_id, pos)
        # 写成交
        self.state.add_trade(
            self.instance_id, code, name, "sell", price, sell_shares,
            proceeds, fee=fee, tax=tax, reason=reason,
        )
        return ExecResult(
            code=code, side="sell", price=price, shares=sell_shares,
            amount=proceeds, fee=fee, tax=tax, success=True, reason=reason,
        )
