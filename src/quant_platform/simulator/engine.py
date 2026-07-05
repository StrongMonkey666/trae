"""模拟持仓主引擎。

每个 tick：
  1. 拉取最新行情（用 data_service.get_realtime_data）
  2. 更新组合市值（mark-to-market）
  3. 检查所有持仓的硬止损 / 止盈 / 持股到期
  4. 若当日是调仓日：基于实时行情特征选股 + 调仓
  5. 写快照
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from ..backtest.allocator import allocate
from ..backtest.position import Position
from ..backtest.strategy import StrategyConfig
from ..data_service.unified_api import UnifiedDataService
from ..selector.engine import SelectorEngine
from ..utils.logger import get_logger
from .executor import PaperExecutor
from .state import SimState

logger = get_logger(__name__)


@dataclass
class SimTick:
    instance_id: int
    timestamp: datetime
    cash: float
    position_value: float
    total_value: float
    pnl: float
    pnl_pct: float
    positions: List[Dict[str, Any]]
    last_actions: List[str] = None  # 本 tick 触发的事件

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(timespec="seconds"),
            "cash": round(self.cash, 2),
            "position_value": round(self.position_value, 2),
            "total_value": round(self.total_value, 2),
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 4),
            "positions": self.positions,
            "actions": self.last_actions or [],
        }


class SimulatedHoldingEngine:
    """模拟持仓主引擎。

    使用方式：
        engine = SimulatedHoldingEngine.from_config(config, instance_id=1)
        engine.start(poll_seconds=3)  # 阻塞
        # 或
        tick = engine.tick_once()
    """

    def __init__(
        self,
        instance_id: int,
        config: StrategyConfig,
        state: SimState,
        data_service: Optional[UnifiedDataService] = None,
        selector_engine: Optional[SelectorEngine] = None,
    ) -> None:
        self.instance_id = instance_id
        self.config = config
        self.state = state
        self.data = data_service or UnifiedDataService()
        self.selector = selector_engine or SelectorEngine()
        self.executor = PaperExecutor(state, instance_id)
        self._lock = threading.RLock()
        self._last_rebalance_date: Optional[date] = None
        self._stop = False

    # ============================================================
    # 单 tick
    # ============================================================
    def tick_once(self) -> SimTick:
        with self._lock:
            now = datetime.now()
            today = now.date()
            actions: List[str] = []

            # 1) 拉取最新行情（持仓代码 + 股票池）
            positions = self.state.get_positions(self.instance_id)
            held_codes = [p.code for p in positions]
            universe = self._universe(held_codes)
            try:
                quotes_df = self.data.get_realtime_data(universe)
            except Exception as e:
                logger.warning("拉取实时行情失败: %s", e)
                quotes_df = pd.DataFrame()
            quotes: Dict[str, Dict[str, Any]] = {}
            if not quotes_df.empty:
                for _, r in quotes_df.iterrows():
                    code = str(r.get("code", "")).zfill(6)
                    if code:
                        quotes[code] = r.to_dict()

            # 2) 计算市值
            cash = self.state.get_cash(self.instance_id)
            position_value = 0.0
            for pos in positions:
                q = quotes.get(pos.code)
                price = float(q["last"]) if q and q.get("last") else pos.avg_cost
                position_value += pos.shares * price
            total_value = cash + position_value
            instance = self.state.get_instance(self.instance_id) or {}
            initial = float(instance.get("initial_capital", 0)) or 0.0
            pnl = total_value - initial
            pnl_pct = (pnl / initial) if initial > 0 else 0.0

            # 3) 监控持仓：止盈/止损/持股到期
            for pos in positions:
                q = quotes.get(pos.code)
                if q is None:
                    continue
                last = float(q.get("last", 0))
                high = float(q.get("high", last))
                low = float(q.get("low", last))
                if last <= 0:
                    continue
                # 更新持仓 peak_price / in_tp_zone
                if high > pos.peak_price:
                    pos.peak_price = high
                if not pos.in_tp_zone and pos.avg_cost > 0 and high >= pos.avg_cost * (
                    1 + self.config.take_profit_threshold
                ):
                    pos.in_tp_zone = True
                self.state.upsert_position(self.instance_id, pos)

                reason = self._sell_reason(pos, last, low, high, today)
                if reason:
                    result = self.executor.sell(
                        pos.code, pos.name, last,
                        reason=reason,
                    )
                    if result.success:
                        actions.append(
                            f"SELL {pos.code} @{last} reason={reason} shares={result.shares}"
                        )

            # 4) 调仓日：选股 + 买入
            if self._is_rebalance_day(today):
                actions.extend(self._rebalance(quotes, today))

            # 5) 写快照
            self.state.save_snapshot(
                self.instance_id, today, cash, position_value,
                total_value, pnl,
            )

            # 6) 持仓摘要
            pos_summary = []
            for pos in self.state.get_positions(self.instance_id):
                q = quotes.get(pos.code, {})
                last = float(q.get("last", 0)) if q else 0
                pos_summary.append({
                    "code": pos.code,
                    "name": pos.name,
                    "shares": pos.shares,
                    "avg_cost": round(pos.avg_cost, 4),
                    "last": last,
                    "profit_pct": round(
                        (last - pos.avg_cost) / pos.avg_cost * 100
                        if pos.avg_cost > 0 else 0, 2
                    ),
                    "in_tp_zone": pos.in_tp_zone,
                })
            return SimTick(
                instance_id=self.instance_id,
                timestamp=now,
                cash=cash,
                position_value=position_value,
                total_value=total_value,
                pnl=pnl,
                pnl_pct=pnl_pct,
                positions=pos_summary,
                last_actions=actions,
            )

    # ============================================================
    # 调仓
    # ============================================================
    def _rebalance(
        self, quotes: Dict[str, Dict[str, Any]], today: date
    ) -> List[str]:
        actions: List[str] = []
        if self._last_rebalance_date == today:
            return actions
        # 1) 卖出条件不符（不再被选中的）
        selected_codes = self._run_selector(quotes)
        positions = self.state.get_positions(self.instance_id)
        held_codes = [p.code for p in positions]
        to_sell_cond = [c for c in held_codes if c not in selected_codes]
        n_sell = 0
        for code in to_sell_cond:
            if n_sell >= self.config.max_sell_per_day:
                break
            q = quotes.get(code)
            if not q:
                continue
            last = float(q.get("last", 0))
            if last <= 0:
                continue
            pos = next((p for p in positions if p.code == code), None)
            if pos is None:
                continue
            r = self.executor.sell(
                code, pos.name, last, reason="condition_fail"
            )
            if r.success:
                actions.append(
                    f"SELL {code} @{last} reason=condition_fail shares={r.shares}"
                )
                n_sell += 1

        # 2) 买入：从选股结果排除已持仓
        positions = self.state.get_positions(self.instance_id)
        held_codes = [p.code for p in positions]
        if len(held_codes) >= self.config.max_holdings:
            return actions
        targets = [c for c in selected_codes if c not in held_codes]
        buyable = self.config.max_holdings - len(held_codes)
        targets = targets[:buyable]
        if not targets:
            return actions

        cash = self.state.get_cash(self.instance_id)
        # 资金分配
        features = self._build_features(quotes)
        alloc = allocate(
            self.config.capital_model, targets, cash,
            features=features,
            sort_by=self.config.selector.sort_by if self.config.selector else None,
            fixed_amount=self.config.fixed_amount,
            kelly_fraction=self.config.kelly_fraction,
        )
        n_buy = 0
        for code in targets:
            if n_buy >= self.config.max_buy_per_day:
                break
            q = quotes.get(code)
            if not q:
                continue
            last = float(q.get("last", 0))
            if last <= 0:
                continue
            amount = alloc.get(code, 0)
            if amount < 1000:
                continue
            r = self.executor.buy(
                code, str(q.get("name", "")), last, amount,
                reason="rebalance",
            )
            if r.success:
                actions.append(
                    f"BUY {code} @{last} shares={r.shares} reason=rebalance"
                )
                n_buy += 1

        self._last_rebalance_date = today
        return actions

    # ============================================================
    # 选股
    # ============================================================
    def _run_selector(
        self, quotes: Dict[str, Dict[str, Any]]
    ) -> List[str]:
        if self.config.selector is None:
            return []
        features = self._build_features(quotes)
        if features.empty:
            return []
        result = self.selector.run(
            self.config.selector, features, exclude_codes=None
        )
        if result.empty:
            return []
        return [
            str(c).zfill(6)
            for c in result["code"].tolist()
        ]

    def _build_features(
        self, quotes: Dict[str, Dict[str, Any]]
    ) -> pd.DataFrame:
        if not quotes:
            return pd.DataFrame()
        rows = []
        for code, q in quotes.items():
            rows.append({
                "code": code,
                "name": str(q.get("name", "")),
                "close": float(q.get("last", 0) or 0),
                "open": float(q.get("open", 0) or 0),
                "high": float(q.get("high", 0) or 0),
                "low": float(q.get("low", 0) or 0),
                "volume": float(q.get("volume", 0) or 0),
                "amount": float(q.get("amount", 0) or 0),
                "change_pct": (
                    (float(q.get("last", 0)) - float(q.get("pre_close", 0)))
                    / float(q.get("pre_close", 1)) * 100
                    if q.get("pre_close") else 0
                ),
                "turnover_rate": float(q.get("turnover_rate", 0) or 0),
                "pe_ttm": float(q.get("pe_ttm", 0) or 0),
                "pb": float(q.get("pb", 0) or 0),
                "market_cap": float(q.get("market_cap", 0) or 0),
            })
        return pd.DataFrame(rows)

    # ============================================================
    # 工具方法
    # ============================================================
    def _universe(self, held_codes: List[str]) -> List[str]:
        """股票池：当前持仓 + 已上市的所有股票。

        优化：先只使用当前持仓；按需扩展到全市场。
        """
        return list(set(held_codes))

    def _is_rebalance_day(self, today: date) -> bool:
        from ..utils.trading_calendar import TradingCalendar
        cal = TradingCalendar()
        if not cal.is_trading_day(today):
            return False
        freq = self.config.rebalance_freq
        # 简化：根据频率 + 日期判断
        if freq == "daily":
            return True
        if freq == "weekly":
            return today.weekday() == 0  # 周一
        if freq == "monthly":
            return today.day == 1
        return False

    def _sell_reason(
        self,
        pos: Position,
        last: float,
        low: float,
        high: float,
        today: date,
    ) -> str:
        if pos.avg_cost <= 0:
            return ""
        # 硬止损（用 low 触发）
        low_profit = (low - pos.avg_cost) / pos.avg_cost
        if low_profit <= self.config.stop_loss:
            return "stop_loss"
        # 止盈观察区回落
        if pos.in_tp_zone and pos.peak_price > 0:
            peak_profit = (pos.peak_price - pos.avg_cost) / pos.avg_cost
            current_profit = (last - pos.avg_cost) / pos.avg_cost
            if peak_profit - current_profit >= self.config.take_profit_drawdown:
                return "take_profit"
        # 持股到期
        if pos.buy_date and (today - pos.buy_date).days >= self.config.max_holding_days:
            return "max_holding_days"
        return ""

    # ============================================================
    # 启动 / 停止
    # ============================================================
    def start(self, poll_seconds: float = 3.0) -> None:
        logger.info("SimulatedHoldingEngine #%d 启动, poll=%.1fs", self.instance_id, poll_seconds)
        self._stop = False
        while not self._stop:
            try:
                tick = self.tick_once()
                logger.info(
                    "tick: value=%.2f pnl=%.2f (%.2f%%) actions=%d",
                    tick.total_value, tick.pnl, tick.pnl_pct * 100, len(tick.last_actions or []),
                )
                self._publish_tick_events(tick)
            except Exception as e:
                logger.exception("tick 异常: %s", e)
            time.sleep(poll_seconds)

    def _publish_tick_events(self, tick: "SimTick") -> None:
        """把本 tick 触发的动作发布到事件总线。"""
        try:
            from ..eventbus.bus import get_bus
            from ..eventbus.events import TradeEvent, SignalEvent
            bus = get_bus()
            for action in tick.last_actions or []:
                # 解析 action 字符串
                if action.startswith("BUY "):
                    parts = action.split()
                    code = parts[1]
                    price = float(parts[2].lstrip("@"))
                    reason = parts[-1].split("=")[-1] if "=" in parts[-1] else "rebalance"
                    bus.publish(TradeEvent(
                        event_type="trade.executed",
                        source="simulator",
                        instance_id=tick.instance_id,
                        code=code, side="buy", price=price,
                        shares=0, amount=0, reason=reason,
                        payload={
                            "instance_id": tick.instance_id,
                            "code": code, "name": "",
                            "side": "buy", "price": price,
                            "shares": 0, "amount": 0,
                            "reason": reason,
                        },
                    ))
                elif action.startswith("SELL "):
                    parts = action.split()
                    code = parts[1]
                    price = float(parts[2].lstrip("@"))
                    reason = parts[3].split("=")[-1] if len(parts) > 3 and "=" in parts[3] else "rule"
                    # 信号事件
                    if reason in ("stop_loss", "take_profit", "max_holding_days", "condition_fail"):
                        bus.publish(SignalEvent(
                            event_type="signal.triggered",
                            source="simulator",
                            instance_id=tick.instance_id,
                            code=code, signal=reason,
                            payload={
                                "instance_id": tick.instance_id,
                                "code": code, "name": "",
                                "signal": reason, "profit_pct": 0,
                            },
                        ))
                    bus.publish(TradeEvent(
                        event_type="trade.executed",
                        source="simulator",
                        instance_id=tick.instance_id,
                        code=code, side="sell", price=price,
                        shares=0, amount=0, reason=reason,
                        payload={
                            "instance_id": tick.instance_id,
                            "code": code, "name": "",
                            "side": "sell", "price": price,
                            "shares": 0, "amount": 0,
                            "reason": reason,
                        },
                    ))
        except Exception as e:
            logger.debug("事件发布失败（不影响主流程）: %s", e)

    def stop(self) -> None:
        self._stop = True
        self.state.update_instance_status(self.instance_id, "stopped")
        logger.info("SimulatedHoldingEngine #%d 停止", self.instance_id)

    # ============================================================
    # 从回测记录部署
    # ============================================================
    @classmethod
    def deploy_from_record(
        cls,
        record_id: int,
        record_store,
        state: SimState,
        data_service: Optional[UnifiedDataService] = None,
        selector_engine: Optional[SelectorEngine] = None,
        name_suffix: str = "",
    ) -> "SimulatedHoldingEngine":
        record = record_store.get(record_id)
        if record is None:
            raise ValueError(f"回测记录 #{record_id} 不存在")
        config = record.config
        # 初始资金 = 回测的最终权益（按用户要求：复现回测收益）
        initial = float(record.metrics.get("final_equity", config.initial_capital))
        name = f"sim_{record.name}{name_suffix}"
        instance_id = state.create_instance(
            name=name,
            config_json=config.to_json(),
            initial_capital=initial,
            backtest_id=record_id,
        )
        record_store.mark_deployed(record_id)
        # 发布部署事件
        try:
            from ..eventbus.bus import get_bus
            from ..eventbus.events import DeployedEvent
            get_bus().publish(DeployedEvent(
                event_type="simulator.deployed",
                source="simulator",
                instance_id=instance_id,
                backtest_id=record_id,
                initial_capital=initial,
                payload={
                    "instance_id": instance_id,
                    "backtest_id": record_id,
                    "initial_capital": initial,
                },
            ))
        except Exception as e:
            logger.debug("部署事件发布失败: %s", e)
        return cls(
            instance_id=instance_id,
            config=config,
            state=state,
            data_service=data_service,
            selector_engine=selector_engine,
        )

    @classmethod
    def from_config(
        cls,
        config: StrategyConfig,
        state: SimState,
        name: str = "manual",
        data_service: Optional[UnifiedDataService] = None,
        selector_engine: Optional[SelectorEngine] = None,
    ) -> "SimulatedHoldingEngine":
        instance_id = state.create_instance(
            name=name,
            config_json=config.to_json(),
            initial_capital=config.initial_capital,
        )
        return cls(
            instance_id=instance_id,
            config=config,
            state=state,
            data_service=data_service,
            selector_engine=selector_engine,
        )
