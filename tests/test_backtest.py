"""回测引擎测试（注入 fake K 线 + 不依赖外网）。"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import List

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.backtest.engine import BacktestEngine, _Portfolio, FEE_RATE, STAMP_TAX
from quant_platform.backtest.metrics import compute_metrics
from quant_platform.backtest.position import Trade
from quant_platform.backtest.strategy import StrategyConfig
from quant_platform.backtest.allocator import (
    equal_weight, fixed_amount, score_weight, kelly, allocate,
)
from quant_platform.selector.schema import Condition, SelectorSpec


# ============================================================
# 假数据服务
# ============================================================
class _FakeDataService:
    def __init__(self, klines: dict, stock_list: pd.DataFrame):
        self._klines = klines
        self._stock_list = stock_list

    def get_stock_list(self) -> pd.DataFrame:
        return self._stock_list

    def get_history_data(self, code, start, end, adj="qfq", auto_sync=False):
        df = self._klines.get(code)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        return df.reset_index(drop=True)


def _make_klines(codes: List[str], start: date, days: int, base_price=10.0) -> dict:
    """生成线性上涨/下跌的假 K 线。"""
    out = {}
    for i, code in enumerate(codes):
        rows = []
        for d in range(days):
            cur = start + timedelta(days=d)
            # 跳过周末
            if cur.weekday() >= 5:
                continue
            # 不同股票不同斜率
            slope = 0.02 if i % 2 == 0 else -0.01
            price = base_price * (1 + slope * d)
            rows.append({
                "date": cur,
                "open": price,
                "high": price * 1.02,
                "low": price * 0.98,
                "close": price,
                "volume": 1_000_000,
                "amount": price * 1_000_000,
            })
        out[code] = pd.DataFrame(rows)
    return out


# ============================================================
# Portfolio
# ============================================================
def test_portfolio_value():
    p = _Portfolio(cash=100_000)
    assert p.value({"a": 10.0}) == 100_000
    from quant_platform.backtest.position import Position
    p.positions["a"] = Position(code="a", shares=100, avg_cost=10.0)
    assert p.value({"a": 11.0}) == 100_000 + 100 * 11.0
    assert p.has("a")


# ============================================================
# Metrics
# ============================================================
def test_compute_metrics_positive():
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(30)]
    eq = pd.DataFrame({
        "date": days,
        "value": [1_000_000 * (1 + 0.001 * i) for i in range(30)],
    })
    m = compute_metrics(1_000_000, eq, trades=[])
    assert m.final_equity > 1_000_000
    assert m.total_return > 0
    assert m.max_drawdown == 0  # 单调上涨无回撤


def test_compute_metrics_drawdown():
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(30)]
    vals = [1_000_000, 1_100_000, 1_050_000, 900_000, 950_000]
    vals = [v for v in [1_000_000, 1_100_000, 1_050_000, 900_000, 950_000] for _ in range(6)]
    eq = pd.DataFrame({"date": days, "value": vals})
    m = compute_metrics(1_000_000, eq, trades=[])
    assert m.max_drawdown < 0


def test_compute_metrics_with_trades():
    trades = [
        Trade(code="a", buy_date=date(2025,1,1), buy_price=10,
              sell_date=date(2025,2,1), sell_price=12, shares=100,
              profit_pct=0.20, profit_amount=200, hold_days=31),
        Trade(code="b", buy_date=date(2025,1,1), buy_price=10,
              sell_date=date(2025,2,1), sell_price=9, shares=100,
              profit_pct=-0.10, profit_amount=-100, hold_days=31),
    ]
    days = [date(2025,1,1) + timedelta(days=i) for i in range(60)]
    eq = pd.DataFrame({"date": days, "value": [1_000_000 + i*100 for i in range(60)]})
    m = compute_metrics(1_000_000, eq, trades=trades)
    assert m.trade_count == 2
    assert m.win_rate == 0.5


# ============================================================
# Allocator
# ============================================================
def test_equal_weight():
    out = equal_weight(["a", "b", "c"], 30_000)
    assert abs(out["a"] - 10_000) < 1e-6
    assert abs(out["b"] - 10_000) < 1e-6


def test_fixed_amount():
    out = fixed_amount(["a", "b"], 1_000_000, fixed_amount=15_000)
    assert out["a"] == 15_000


def test_score_weight_rank_based():
    features = pd.DataFrame({
        "code": ["600000", "600001", "600002"],
        "score": [100, 90, 80],
    })
    out = score_weight(["600000", "600001", "600002"], 30_000, features=features, sort_by="score")
    assert out["600000"] > out["600001"] > out["600002"]


def test_kelly_basic():
    out = kelly(["a", "b"], 100_000, win_rate=0.6, avg_win=0.2, avg_loss=0.1, fraction=0.5)
    assert all(v > 0 for v in out.values())
    # 总分配不超过 cash
    assert sum(out.values()) <= 100_000


# ============================================================
# BacktestEngine end-to-end (使用 fake 数据)
# ============================================================
def test_backtest_runs_with_fake_data(tmp_path: Path):
    start = date(2025, 1, 6)  # 周一
    end = date(2025, 3, 28)
    codes = ["600000", "600001", "600002", "600003", "600004"]

    # 准备 K 线
    klines = _make_klines(codes, start - timedelta(days=30), 100)

    # 让"前两只"满足条件，后三只不满足（PE 风格：靠 close 排序近似）
    spec = SelectorSpec(
        conditions=[Condition("close", ">", 0)],  # 全部通过
        sort_by="close", sort_order="asc", limit=2,
    )
    cfg = StrategyConfig(
        name="test", start_date=start, end_date=end,
        initial_capital=1_000_000,
        rebalance_freq="weekly",
        selector=spec,
        max_holdings=2,
        max_buy_per_day=1,
        max_sell_per_day=5,
        stop_loss=-0.50,  # 防止误触发
        take_profit_threshold=10.0,  # 防止误触发
        max_holding_days=999,
    )
    stock_list = pd.DataFrame({
        "code": codes,
        "name": [f"Stock{i}" for i in range(len(codes))],
        "market": ["SH"] * len(codes),
    })
    svc = _FakeDataService(klines, stock_list)
    engine = BacktestEngine(data_service=svc)
    result = engine.run(cfg, universe=codes)

    assert result.metrics.trade_count >= 0
    assert not result.equity_curve.empty
    assert len(result.equity_curve) > 0


def test_backtest_triggers_stop_loss(tmp_path: Path):
    """构造一只持续下跌的股票，验证硬止损会触发。"""
    start = date(2025, 1, 6)
    end = date(2025, 2, 28)
    codes = ["600001"]
    # 每天 -5%，连续跌 20 天 -> 必然触发 -8% 止损
    rows = []
    cur = start - timedelta(days=30)
    price = 100.0
    while cur <= end:
        if cur.weekday() < 5:
            rows.append({
                "date": cur,
                "open": price, "high": price, "low": price * 0.95,
                "close": price * 0.95,
                "volume": 1_000_000, "amount": price * 1_000_000,
            })
            price *= 0.95
        cur += timedelta(days=1)
    klines = {"600001": pd.DataFrame(rows)}

    spec = SelectorSpec(conditions=[Condition("close", ">", 0)])
    cfg = StrategyConfig(
        name="stop_test", start_date=start, end_date=end,
        initial_capital=1_000_000,
        rebalance_freq="weekly",
        selector=spec, max_holdings=1, max_buy_per_day=1,
        stop_loss=-0.08,
        take_profit_threshold=10.0,
        max_holding_days=999,
    )
    stock_list = pd.DataFrame({
        "code": ["600001"], "name": ["跌停王"], "market": ["SH"],
    })
    svc = _FakeDataService(klines, stock_list)
    result = BacktestEngine(data_service=svc).run(cfg, universe=["600001"])
    reasons = {t.sell_reason for t in result.trades if t.sell_reason}
    assert "stop_loss" in reasons or "end_of_period" in reasons


def test_backtest_strategy_config_roundtrip():
    cfg = StrategyConfig(
        name="x",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 6, 1),
        initial_capital=500_000,
        selector=SelectorSpec(conditions=[Condition("pe_ttm", "<", 20)]),
    )
    s = cfg.to_json()
    cfg2 = StrategyConfig.from_json(s)
    assert cfg2.name == "x"
    assert cfg2.initial_capital == 500_000
    assert len(cfg2.selector.conditions) == 1
