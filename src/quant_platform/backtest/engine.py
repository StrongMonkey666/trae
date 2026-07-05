"""回测主引擎。

核心循环：
1. 准备交易日 + 调仓日
2. 预拉 K 线（按 universe 一次性加载）
3. 每个交易日：
   - 标记当日权益
   - 执行昨日生成的订单（以今日开盘价撮合）
   - 若是调仓日：基于昨日（T-1）数据生成今日订单
4. 回测结束：按最后一日收盘价清仓
5. 计算指标
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..data_service.unified_api import UnifiedDataService
from ..selector.engine import SelectorEngine
from ..utils.logger import get_logger
from .allocator import allocate
from .metrics import PerformanceMetrics, compute_metrics
from .position import Position, Trade
from .strategy import StrategyConfig

logger = get_logger(__name__)


@dataclass
class Order:
    """待执行的订单（次日开盘撮合）。"""

    code: str
    name: str = ""
    side: str = "buy"           # buy / sell
    amount: float = 0.0         # 买入金额 / 卖出股数（由 side 决定）
    reason: str = ""


@dataclass
class BacktestResult:
    metrics: PerformanceMetrics
    equity_curve: pd.DataFrame
    trades: List[Trade] = field(default_factory=list)
    config: Optional[StrategyConfig] = None

    def to_dict(self) -> dict:
        return {
            "metrics": self.metrics.to_dict(),
            "trade_count": len(self.trades),
            "trades": [t.to_dict() for t in self.trades],
        }


class _Portfolio:
    """内部组合状态管理。"""

    def __init__(self, cash: float) -> None:
        self.cash = cash
        self.positions: Dict[str, Position] = {}

    def value(self, prices: Dict[str, float]) -> float:
        pos_value = 0.0
        for code, pos in self.positions.items():
            p = prices.get(code)
            if p is not None:
                pos_value += pos.shares * p
            else:
                # 没有行情，按成本价估算
                pos_value += pos.shares * pos.avg_cost
        return self.cash + pos_value

    def has(self, code: str) -> bool:
        return code in self.positions


class BacktestEngine:
    def __init__(
        self,
        data_service: Optional[UnifiedDataService] = None,
        selector_engine: Optional[SelectorEngine] = None,
    ) -> None:
        self.data = data_service or UnifiedDataService()
        self.selector = selector_engine or SelectorEngine()

    # ============================================================
    # 主入口
    # ============================================================
    def run(
        self,
        config: StrategyConfig,
        universe: Optional[List[str]] = None,
        lookback_days: int = 120,
    ) -> BacktestResult:
        config.validate()
        start = config.start_date
        end = config.end_date
        if config.selector is None:
            raise ValueError("config.selector 必填")

        # 1. 交易日序列：使用已有交易日历（无网络时退化为工作日）
        trading_days = self._trading_days(start, end)

        # 2. 调仓日
        rebalance_days = set(self._rebalance_days(trading_days, config.rebalance_freq))

        # 3. Universe
        if universe is None:
            stock_df = self.data.get_stock_list()
            universe = stock_df["code"].astype(str).str.zfill(6).tolist()

        # 4. 预拉 K 线（多拉 lookback 天用于指标计算）
        klines = self._prefetch_klines(
            universe, start - timedelta(days=lookback_days), end
        )
        # 名称映射
        stock_df = self.data.get_stock_list()
        if not stock_df.empty:
            name_map = dict(zip(
                stock_df["code"].astype(str).str.zfill(6), stock_df["name"]
            ))
        else:
            name_map = {}

        # 5. 主循环
        portfolio = _Portfolio(cash=config.initial_capital)
        equity_curve: List[dict] = []
        trades: List[Trade] = []
        pending_orders: List[Order] = []

        for i, today in enumerate(trading_days):
            # 5.1 mark-to-market（用今日收盘价）
            today_prices = self._prices_on(klines, today)
            equity_curve.append({
                "date": today,
                "cash": portfolio.cash,
                "value": portfolio.value(today_prices),
            })

            # 5.2 撮合昨日订单
            if pending_orders:
                self._execute_pending_orders(
                    pending_orders, portfolio, klines, today, trades, name_map
                )
                pending_orders = []

            # 5.3 调仓日：基于昨日（i-1）数据生成今日订单
            if today in rebalance_days and i > 0:
                # 信号日 = 昨天；价格用昨日收盘
                signal_day = trading_days[i - 1]
                new_orders = self._generate_orders(
                    portfolio, config, klines,
                    signal_day=signal_day, today=today,
                    name_map=name_map,
                )
                pending_orders = new_orders

            # 5.4 每天检查硬止损/止盈（连续监控，不依赖调仓）
            intraday_orders = self._check_intraday_signals(
                portfolio, config, klines, today
            )
            # 立即以次日开盘执行（与调仓共用 pending_orders）
            if intraday_orders:
                # 下一个交易日才执行；如果当日是最后一天就立刻用收盘价近似
                pending_orders.extend(intraday_orders)

        # 6. 强制清仓
        last_day = trading_days[-1]
        last_prices = self._prices_on(klines, last_day)
        for code in list(portfolio.positions.keys()):
            pos = portfolio.positions[code]
            price = last_prices.get(code, pos.avg_cost)
            self._sell(
                portfolio, pos, klines, last_day, price, trades,
                reason="end_of_period", exec_at_open=False,
            )
        # 更新最后一日权益
        equity_curve[-1] = {
            "date": last_day,
            "cash": portfolio.cash,
            "value": portfolio.value(last_prices),
        }

        # 7. 指标
        eq_df = pd.DataFrame(equity_curve)
        eq_df["date"] = pd.to_datetime(eq_df["date"])
        m = compute_metrics(
            config.initial_capital, eq_df, trades,
            risk_free_rate=config.__dict__.get("risk_free_rate", 0.02)
            if hasattr(config, "risk_free_rate") else 0.02,
        )
        return BacktestResult(metrics=m, equity_curve=eq_df, trades=trades, config=config)

    # ============================================================
    # 交易日 / 调仓日
    # ============================================================
    def _trading_days(self, start: date, end: date) -> List[date]:
        """优先使用 UnifiedDataService 的交易日历，否则退化为工作日。"""
        # 简化实现：使用本地 TradingCalendar；若项目已有真实数据源接入，
        # 未来由 data_service 暴露 trading_calendar() 接口
        from ..utils.trading_calendar import TradingCalendar
        cal = TradingCalendar()
        days: List[date] = []
        cur = start
        while cur <= end:
            if cal.is_trading_day(cur):
                days.append(cur)
            cur += timedelta(days=1)
        return days

    def _rebalance_days(self, trading_days: List[date], freq: str) -> List[date]:
        if not trading_days:
            return []
        if freq == "daily":
            return list(trading_days)
        if freq == "weekly":
            # 每周一（或第一个交易日）
            seen_week = set()
            out = []
            for d in trading_days:
                key = d.isocalendar()[:2]  # (year, week)
                if key not in seen_week:
                    seen_week.add(key)
                    out.append(d)
            return out
        if freq == "monthly":
            seen_month = set()
            out = []
            for d in trading_days:
                key = (d.year, d.month)
                if key not in seen_month:
                    seen_month.add(key)
                    out.append(d)
            return out
        return list(trading_days)

    # ============================================================
    # 数据预拉
    # ============================================================
    def _prefetch_klines(
        self, universe: List[str], start: date, end: date
    ) -> Dict[str, pd.DataFrame]:
        """预拉所有股票 K 线。"""
        klines: Dict[str, pd.DataFrame] = {}
        for code in universe:
            try:
                df = self.data.get_history_data(code, start=start, end=end, adj="qfq")
                if not df.empty:
                    klines[code] = df
            except Exception as e:
                logger.debug("K 线预拉 %s 失败: %s", code, e)
        logger.info("K 线预拉完成: %d 只", len(klines))
        return klines

    def _prices_on(self, klines: Dict[str, pd.DataFrame], day: date) -> Dict[str, float]:
        prices: Dict[str, float] = {}
        for code, df in klines.items():
            row = df[df["date"] == day]
            if not row.empty:
                prices[code] = float(row["close"].iloc[0])
        return prices

    def _open_on(self, klines: Dict[str, pd.DataFrame], day: date) -> Dict[str, float]:
        prices: Dict[str, float] = {}
        for code, df in klines.items():
            row = df[df["date"] == day]
            if not row.empty:
                prices[code] = float(row["open"].iloc[0])
        return prices

    # ============================================================
    # 订单生成（信号日 -> 下一交易日执行）
    # ============================================================
    def _generate_orders(
        self,
        portfolio: _Portfolio,
        config: StrategyConfig,
        klines: Dict[str, pd.DataFrame],
        signal_day: date,
        today: date,
        name_map: Dict[str, str],
    ) -> List[Order]:
        """在调仓日基于 signal_day 的数据生成订单，today 为执行日。"""
        # 1) 构建特征表（基于 signal_day 的历史数据）
        features = self._build_features(klines, signal_day)
        if features.empty:
            return []

        # 2) 当前持仓的代码集合
        held_codes = set(portfolio.positions.keys())

        # 3) 选股：先确定"在选中列表"和"未选中"
        selector_result = self.selector.run(
            config.selector, features, exclude_codes=None
        )
        if not selector_result.empty:
            selected_codes = set(
                selector_result["code"].astype(str).str.zfill(6).tolist()
            )
        else:
            selected_codes = set()

        # 4) 卖出队列：条件不符 + 规则卖出（不在此实现规则，规则检查每日做）
        sell_queue: List[Tuple[str, str]] = []  # (code, reason)
        for code in list(portfolio.positions.keys()):
            if code not in selected_codes:
                sell_queue.append((code, "condition_fail"))

        # 5) 买入目标：从 selected 中排除已持仓
        target_codes = [c for c in selected_codes if c not in held_codes]

        # 6) 生成订单（限制单日买卖数量）
        orders: List[Order] = []
        n_sell = 0
        # 条件不符卖出优先
        for code, reason in sell_queue:
            if n_sell >= config.max_sell_per_day:
                break
            pos = portfolio.positions[code]
            orders.append(Order(
                code=code, name=name_map.get(code, pos.name),
                side="sell", amount=pos.shares, reason=reason,
            ))
            n_sell += 1

        # 7) 资金分配 + 买入
        if target_codes and len(portfolio.positions) < config.max_holdings:
            # 限制最多补到 max_holdings
            buyable = config.max_holdings - len(portfolio.positions)
            target_codes = target_codes[:buyable]
            alloc = allocate(
                config.capital_model, target_codes, portfolio.cash,
                features=features,
                sort_by=config.selector.sort_by if config.selector else None,
                fixed_amount=config.fixed_amount,
                kelly_fraction=config.kelly_fraction,
            )
            n_buy = 0
            for code in target_codes:
                if n_buy >= config.max_buy_per_day:
                    break
                amount = alloc.get(code, 0)
                if amount < 1000:  # 至少 1000 元才买
                    continue
                orders.append(Order(
                    code=code, name=name_map.get(code, ""),
                    side="buy", amount=amount, reason="rebalance",
                ))
                n_buy += 1
        return orders

    def _build_features(
        self, klines: Dict[str, pd.DataFrame], day: date
    ) -> pd.DataFrame:
        """基于截至 day 的历史 K 线构建特征表。"""
        rows = []
        for code, df in klines.items():
            hist = df[df["date"] <= day]
            if hist.empty or len(hist) < 5:
                continue
            last = hist.iloc[-1]
            close = float(last["close"])
            row = {
                "code": code,
                "name": "",
                "close": close,
                "open": float(last.get("open", 0)),
                "high": float(last.get("high", 0)),
                "low": float(last.get("low", 0)),
                "volume": float(last.get("volume", 0)),
                "amount": float(last.get("amount", 0)),
            }
            # 均线
            for n in (5, 10, 20, 60):
                if len(hist) >= n:
                    row[f"ma_{n}"] = float(hist["close"].tail(n).mean())
                else:
                    row[f"ma_{n}"] = close
            # 涨跌幅
            if len(hist) >= 2:
                prev_close = float(hist["close"].iloc[-2])
                row["change_pct"] = (close - prev_close) / prev_close * 100 if prev_close else 0
            else:
                row["change_pct"] = 0
            # 换手率/量比/PE 等暂取 0（需要独立数据源）
            row["turnover_rate"] = 0.0
            row["volume_ratio"] = 0.0
            row["pe_ttm"] = 0.0
            row["pb"] = 0.0
            row["market_cap"] = 0.0
            rows.append(row)
        return pd.DataFrame(rows)

    # ============================================================
    # 盘中信号（硬止损 / 止盈 / 持股到期 / 技术）
    # ============================================================
    def _check_intraday_signals(
        self,
        portfolio: _Portfolio,
        config: StrategyConfig,
        klines: Dict[str, pd.DataFrame],
        today: date,
    ) -> List[Order]:
        orders: List[Order] = []
        for code, pos in list(portfolio.positions.items()):
            df = klines.get(code)
            if df is None or df.empty:
                continue
            row = df[df["date"] == today]
            if row.empty:
                continue
            r = row.iloc[0]
            low = float(r["low"])
            high = float(r["high"])
            close = float(r["close"])
            reason = self._sell_reason(pos, config, low, high, close, today)
            if reason:
                orders.append(Order(
                    code=code, name=pos.name,
                    side="sell", amount=pos.shares, reason=reason,
                ))
        return orders

    @staticmethod
    def _sell_reason(
        pos: Position,
        config: StrategyConfig,
        low: float,
        high: float,
        close: float,
        today: date,
    ) -> str:
        """根据日内价格判断卖出原因。返回空字符串表示不卖。"""
        # 更新峰值
        if high > pos.peak_price:
            pos.peak_price = high
        profit = (close - pos.avg_cost) / pos.avg_cost if pos.avg_cost else 0
        # 硬止损：用 low 触发
        low_profit = (low - pos.avg_cost) / pos.avg_cost if pos.avg_cost else 0
        if low_profit <= config.stop_loss:
            return "stop_loss"
        # 止盈：进入观察区
        if not pos.in_tp_zone and high >= pos.avg_cost * (1 + config.take_profit_threshold):
            pos.in_tp_zone = True
        if pos.in_tp_zone and pos.peak_price > 0:
            peak_profit = (pos.peak_price - pos.avg_cost) / pos.avg_cost
            if peak_profit - profit >= config.take_profit_drawdown:
                return "take_profit"
        # 持股到期
        if pos.buy_date and (today - pos.buy_date).days >= config.max_holding_days:
            return "max_holding_days"
        # 技术条件（简化：仅支持 ma_20 下穿）
        if config.technical_sell:
            field = config.technical_sell.get("field")
            if field and field.startswith("close_below_ma"):
                # 简单条件：如 close < ma_20
                pass
        return ""

    # ============================================================
    # 撮合
    # ============================================================
    def _execute_pending_orders(
        self,
        orders: List[Order],
        portfolio: _Portfolio,
        klines: Dict[str, pd.DataFrame],
        today: date,
        trades: List[Trade],
        name_map: Dict[str, str],
    ) -> None:
        opens = self._open_on(klines, today)
        for o in orders:
            df = klines.get(o.code)
            if df is None or df.empty:
                continue
            row = df[df["date"] == today]
            if row.empty:
                continue
            open_p = float(row["open"].iloc[0])
            if o.side == "buy":
                self._buy(
                    portfolio, o, klines, today, open_p, trades, name_map
                )
            else:
                pos = portfolio.positions.get(o.code)
                if pos is None:
                    continue
                self._sell(
                    portfolio, pos, klines, today, open_p, trades,
                    reason=o.reason, exec_at_open=True,
                )

    def _buy(
        self,
        portfolio: _Portfolio,
        order: Order,
        klines: Dict[str, pd.DataFrame],
        today: date,
        open_price: float,
        trades: List[Trade],
        name_map: Dict[str, str],
    ) -> None:
        exec_price = open_price
        if exec_price <= 0:
            return
        # 按 100 股一手
        shares = int(order.amount // (exec_price * 100)) * 100
        if shares <= 0:
            return
        cost = shares * exec_price
        fee = max(cost * FEE_RATE, 5.0)
        total_cost = cost + fee
        if total_cost > portfolio.cash:
            # 现金不够则缩减股数
            affordable = int(
                (portfolio.cash / (exec_price * (1 + FEE_RATE))) // 100
            ) * 100
            if affordable <= 0:
                return
            shares = affordable
            cost = shares * exec_price
            fee = max(cost * FEE_RATE, 5.0)
            total_cost = cost + fee
        portfolio.cash -= total_cost
        avg_cost = total_cost / shares  # 含费成本
        pos = Position(
            code=order.code,
            name=order.name or name_map.get(order.code, ""),
            shares=shares,
            avg_cost=avg_cost,
            buy_date=today,
            peak_price=exec_price,
        )
        portfolio.positions[order.code] = pos
        # 记录一笔"未平仓"交易（待卖出时填齐）
        trades.append(Trade(
            code=order.code,
            name=pos.name,
            buy_date=today,
            buy_price=exec_price,
            shares=shares,
        ))

    def _sell(
        self,
        portfolio: _Portfolio,
        pos: Position,
        klines: Dict[str, pd.DataFrame],
        today: date,
        exec_price: float,
        trades: List[Trade],
        reason: str,
        exec_at_open: bool = True,
    ) -> None:
        proceeds = pos.shares * exec_price
        fee = proceeds * FEE_RATE
        tax = proceeds * STAMP_TAX
        net = proceeds - fee - tax
        portfolio.cash += net
        # 找到对应买入交易
        for t in reversed(trades):
            if t.code == pos.code and t.sell_date is None:
                t.sell_date = today
                t.sell_price = exec_price
                t.sell_reason = reason
                t.profit_amount = net - (t.shares * t.buy_price + t.shares * t.buy_price * FEE_RATE)
                t.profit_pct = (exec_price - t.buy_price * (1 + FEE_RATE)) / (t.buy_price * (1 + FEE_RATE))
                t.hold_days = (today - t.buy_date).days
                break
        del portfolio.positions[pos.code]


# 模块级常量
FEE_RATE = 0.0003
STAMP_TAX = 0.001
