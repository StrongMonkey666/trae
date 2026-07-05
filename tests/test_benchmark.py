"""性能/正确性基准测试。

提供两类基准：

A) 性能基准（@pytest.mark.slow）
   验证大数据量下回测在合理时间内完成，防止代码退化。
   默认 skip，需用 `pytest -m slow` 启用。

B) 正确性基准（@pytest.mark.benchmark）
   用手算的预期值校验回测引擎/指标/分配器的输出。
   当回测逻辑变更时应主动复核这些值。
"""
from __future__ import annotations

import math
import time
from datetime import date, timedelta
from typing import Dict, List

import numpy as np
import pandas as pd
import pytest


# ============================================================
# 公共 helper：构造人造 K 线（不依赖网络）
# ============================================================
def _linear_kline(code: str, start: date, days: int,
                  p0: float, slope: float) -> pd.DataFrame:
    """生成 p(t) = p0 * (1 + slope * t_days) 的工作日 K 线。"""
    rows = []
    cur = start
    end = start + timedelta(days=days)
    t = 0
    while cur <= end:
        if cur.weekday() < 5:
            price = p0 * (1 + slope * t)
            rows.append({
                "date": cur,
                "open": price, "high": price * 1.01,
                "low": price * 0.99, "close": price,
                "volume": 1_000_000, "amount": price * 1_000_000,
            })
            t += 1
        cur += timedelta(days=1)
    return pd.DataFrame(rows)


def _build_engine(codes, start, days, prices):
    """构造一个 BacktestEngine + 假数据服务。"""
    from quant_platform.backtest.engine import BacktestEngine
    klines = {
        c: _linear_kline(c, start, days, p, s)
        for c, (p, s) in zip(codes, prices)
    }
    stock_list = pd.DataFrame({
        "code": codes,
        "name": [f"S{i}" for i in range(len(codes))],
        "market": ["SH"] * len(codes),
    })

    class _Svc:
        def get_stock_list(self_inner):
            return stock_list

        def get_history_data(self_inner, code, start, end, adj="qfq", auto_sync=False):
            df = klines.get(code)
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"]).dt.date
            return df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)

        def get_realtime_data(self_inner, codes=None):
            return pd.DataFrame()

    return BacktestEngine(data_service=_Svc()), klines


# ============================================================
# A. 正确性基准（无需 marker，永远跑）
# ============================================================
class TestAllocatorCorrectness:
    """4 种分配模型的预期值是手算的。"""

    def test_equal_weight_split(self):
        from quant_platform.backtest.allocator import equal_weight
        out = equal_weight(["600000", "600001", "600002"], 90_000)
        assert out == {"600000": 30_000.0, "600001": 30_000.0, "600002": 30_000.0}

    def test_equal_weight_empty(self):
        from quant_platform.backtest.allocator import equal_weight
        assert equal_weight([], 1000) == {}
        assert equal_weight(["600000"], 0) == {}

    def test_fixed_amount_caps_cash(self):
        from quant_platform.backtest.allocator import fixed_amount
        out = fixed_amount(["600000", "600001"], available_cash=5000, fixed_amount=10_000)
        # 每只 cap 在 5000
        assert out == {"600000": 5000.0, "600001": 5000.0}

    def test_score_weight_harmonic(self):
        """1/1 + 1/2 + 1/3 = 11/6，rank 1 应占总资金 6/11。"""
        from quant_platform.backtest.allocator import score_weight
        feats = pd.DataFrame({
            "code": ["600000", "600001", "600002"],
            "score": [100, 80, 60],  # 600000 排第 1
        })
        out = score_weight(
            ["600000", "600001", "600002"], 110_000,
            features=feats, sort_by="score",
        )
        # rank 1: 110000 * (1/1) / (11/6) = 110000 * 6/11 = 60000
        # rank 2: 110000 * (1/2) / (11/6) = 30000
        # rank 3: 110000 * (1/3) / (11/6) = 20000
        assert math.isclose(out["600000"], 60_000, rel_tol=1e-6)
        assert math.isclose(out["600001"], 30_000, rel_tol=1e-6)
        assert math.isclose(out["600002"], 20_000, rel_tol=1e-6)
        # 总和等于可用现金（浮点容差）
        assert math.isclose(sum(out.values()), 110_000, rel_tol=1e-6)

    def test_kelly_fractional(self):
        """win=0.6, win_amt=0.1, loss_amt=0.05, fraction=0.5
        b=2, f=(0.6*2-0.4)/2=0.4, after cap*0.5=0.2
        per=available*0.2/3
        """
        from quant_platform.backtest.allocator import kelly
        out = kelly(
            ["600000", "600001", "600002"],
            available_cash=300_000,
            win_rate=0.6, avg_win=0.10, avg_loss=0.05, fraction=0.5,
        )
        expected_per = 300_000 * 0.2 / 3
        for c in ("600000", "600001", "600002"):
            assert math.isclose(out[c], expected_per, rel_tol=1e-6)


class TestMetricsCorrectness:
    """手算的 equity curve 喂进 compute_metrics，验证输出。"""

    def _curve(self, values):
        # 与 values 等长的工作日
        days = [date(2025, 1, d) for d in range(1, 1 + len(values))]
        return pd.DataFrame({"date": days, "value": values})

    def test_total_return(self):
        from quant_platform.backtest.metrics import compute_metrics
        ec = self._curve([100, 110, 121, 100, 110, 120, 130, 125, 135, 150])
        m = compute_metrics(100, ec, [])
        assert math.isclose(m.total_return, 0.5, rel_tol=1e-6)
        assert math.isclose(m.final_equity, 150, rel_tol=1e-6)

    def test_max_drawdown_known(self):
        """曲线 100 -> 110 -> 121 -> 100 -> 150
        peak=121, drawdown at 100: (100-121)/121 = -0.17355...
        """
        from quant_platform.backtest.metrics import compute_metrics
        ec = self._curve([100, 110, 121, 100, 150, 140, 160, 180, 170, 200])
        m = compute_metrics(100, ec, [])
        expected = (100 - 121) / 121
        assert math.isclose(m.max_drawdown, expected, rel_tol=1e-6)

    def test_annualized_return(self):
        """1 年从 100 -> 150，年化 = 50%。"""
        from quant_platform.backtest.metrics import compute_metrics
        days = [date(2024, 1, 1) + timedelta(days=int(365.25 * i / 10)) for i in range(11)]
        ec = pd.DataFrame({
            "date": days,
            "value": [100 * (1.5 ** (i / 10)) for i in range(11)],
        })
        m = compute_metrics(100, ec, [])
        assert math.isclose(m.annualized_return, 0.5, rel_tol=1e-3)

    def test_win_rate_and_avg(self):
        from quant_platform.backtest.metrics import compute_metrics
        from quant_platform.backtest.position import Trade
        ec = self._curve([100, 110, 100, 110, 100])
        trades = [
            Trade(code="1", buy_date=date(2025, 1, 1), buy_price=10,
                  sell_date=date(2025, 1, 5), sell_price=11, shares=100,
                  profit_pct=0.10, profit_amount=100, hold_days=4),
            Trade(code="1", buy_date=date(2025, 1, 6), buy_price=10,
                  sell_date=date(2025, 1, 10), sell_price=9, shares=100,
                  profit_pct=-0.10, profit_amount=-100, hold_days=4),
            Trade(code="1", buy_date=date(2025, 1, 11), buy_price=10,
                  sell_date=date(2025, 1, 15), sell_price=12, shares=100,
                  profit_pct=0.20, profit_amount=200, hold_days=4),
        ]
        m = compute_metrics(100, ec, trades)
        assert m.trade_count == 3
        assert math.isclose(m.win_rate, 2 / 3, rel_tol=1e-6)
        assert math.isclose(m.avg_profit_pct, (0.10 - 0.10 + 0.20) / 3, rel_tol=1e-6)
        assert math.isclose(m.avg_hold_days, 4.0, rel_tol=1e-6)


class TestBacktestInvariants:
    """回测不变量：单标的、单调上涨 -> 期末权益 > 初始。"""

    def test_monotonic_up_profit(self):
        """1 只股票每天涨 1%，每周调仓 -> 期末权益单调上升（接近 1%^N - 手续费）。"""
        engine, _ = _build_engine(
            ["600000"], date(2024, 9, 1), days=200,
            prices=[(10.0, 0.01)],
        )
        from quant_platform.backtest.strategy import StrategyConfig
        from quant_platform.selector.schema import Condition, SelectorSpec
        cfg = StrategyConfig(
            name="mono", start_date=date(2025, 1, 1), end_date=date(2025, 1, 24),
            initial_capital=100_000, rebalance_freq="weekly",
            selector=SelectorSpec(conditions=[Condition("close", ">", 0)]),
            max_holdings=1, max_buy_per_day=1, max_sell_per_day=100,
            stop_loss=-0.99, take_profit_threshold=100.0,  # 关闭硬止盈/止损
        )
        result = engine.run(cfg, universe=["600000"])
        # 期末权益必须 > 初始（股票在涨）
        assert result.metrics.final_equity > 100_000
        # 最大回撤应该很小（没有真正的回撤）
        assert result.metrics.max_drawdown >= -0.01

    def test_cash_conservation(self):
        """max_buy_per_day=0 时不交易，期末权益 = 初始资金。"""
        engine, _ = _build_engine(
            ["600000"], date(2024, 9, 1), days=200,
            prices=[(10.0, 0.0)],
        )
        from quant_platform.backtest.strategy import StrategyConfig
        from quant_platform.selector.schema import Condition, SelectorSpec
        cfg = StrategyConfig(
            name="flat", start_date=date(2025, 1, 1), end_date=date(2025, 1, 24),
            initial_capital=100_000, rebalance_freq="weekly",
            selector=SelectorSpec(conditions=[Condition("close", ">", 0)]),
            max_holdings=1, max_buy_per_day=0, max_sell_per_day=0,
            stop_loss=-0.99, take_profit_threshold=100.0,
        )
        result = engine.run(cfg, universe=["600000"])
        # 没有任何买卖，权益应该完全等于初始（无市值波动情况下）
        assert math.isclose(result.metrics.final_equity, 100_000, rel_tol=1e-6)
        # 没有任何 trade
        assert result.trades == []

    def test_stop_loss_triggered(self):
        """硬止损：买入后任意下跌，下一日卖出。"""
        engine, _ = _build_engine(
            ["600000"], date(2024, 9, 1), days=200,
            prices=[(10.0, 0.0)],  # 价格不变，但配合 stop_loss=0 应触发
        )
        from quant_platform.backtest.strategy import StrategyConfig
        from quant_platform.selector.schema import Condition, SelectorSpec
        # 第一个交易日（day 1）开仓，之后立即触发
        cfg = StrategyConfig(
            name="sl", start_date=date(2025, 1, 1), end_date=date(2025, 2, 14),
            initial_capital=100_000, rebalance_freq="weekly",
            selector=SelectorSpec(conditions=[Condition("close", ">", 0)]),
            max_holdings=1, max_buy_per_day=1, max_sell_per_day=10,
            stop_loss=0.0, take_profit_threshold=100.0,  # 任意下跌即止损
        )
        result = engine.run(cfg, universe=["600000"])
        # 至少应有过 trade
        assert len(result.trades) >= 1


# ============================================================
# B. 性能基准（@pytest.mark.slow）
# ============================================================
class TestPerformance:
    """验证回测引擎的性能基线。

    阈值保守预留 3-5x buffer；如机器明显变慢再放宽。
    """

    @pytest.mark.slow
    @pytest.mark.benchmark
    def test_backtest_50_stocks_1y_under_10s(self):
        """50 只股票 × 1 年日线回测应 < 10s。"""
        codes = [f"{600000 + i:06d}" for i in range(50)]
        engine, _ = _build_engine(
            codes, date(2024, 1, 1), 365,
            prices=[(10.0 + i * 0.1, 0.001 * ((i % 5) - 2)) for i in range(50)],
        )
        from quant_platform.backtest.strategy import StrategyConfig
        from quant_platform.selector.schema import Condition, SelectorSpec
        cfg = StrategyConfig(
            name="perf50", start_date=date(2024, 2, 1), end_date=date(2024, 12, 31),
            initial_capital=10_000_000, rebalance_freq="weekly",
            selector=SelectorSpec(conditions=[Condition("close", ">", 0)]),
            max_holdings=10,
        )
        t0 = time.perf_counter()
        result = engine.run(cfg, universe=codes)
        elapsed = time.perf_counter() - t0
        assert elapsed < 10.0, f"回测耗时 {elapsed:.2f}s，超过 10s 阈值"
        assert not result.equity_curve.empty
        # 报告
        print(f"\n[perf50] 50 stocks × 1y 回测耗时 = {elapsed*1000:.0f}ms")

    @pytest.mark.slow
    @pytest.mark.benchmark
    def test_selector_5k_stocks_under_1s(self):
        """5000 只股票 × 5 条件选股 < 1s。"""
        from quant_platform.selector.engine import SelectorEngine
        from quant_platform.selector.schema import Condition, SelectorSpec
        n = 5000
        np.random.seed(42)
        df = pd.DataFrame({
            "code": [f"{600000 + i:06d}" for i in range(n)],
            "name": [f"S{i}" for i in range(n)],
            "pe_ttm": np.random.uniform(5, 50, n),
            "roe": np.random.uniform(0, 30, n),
            "pb": np.random.uniform(0.5, 10, n),
            "market_cap": np.random.uniform(1e9, 1e12, n),
        })
        spec = SelectorSpec(
            conditions=[
                Condition("pe_ttm", "<", 20),
                Condition("roe", ">", 10),
                Condition("pb", "<", 5),
                Condition("market_cap", ">", 1e10),
                Condition("pe_ttm", ">", 0),
            ],
            logic="AND", sort_by="pe_ttm", sort_order="asc", limit=50,
        )
        eng = SelectorEngine()
        t0 = time.perf_counter()
        out = eng.run(spec, df)
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, f"选股耗时 {elapsed:.2f}s，超过 1s 阈值"
        print(f"\n[perf selector] 5k stocks × 5 conditions = {elapsed*1000:.0f}ms, hits={len(out)}")
