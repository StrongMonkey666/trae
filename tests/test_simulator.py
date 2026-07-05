"""模拟持仓系统测试。"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.backtest.position import Position
from quant_platform.backtest.records import BacktestRecordStore, BacktestRecord
from quant_platform.backtest.strategy import StrategyConfig
from quant_platform.selector.schema import Condition, SelectorSpec
from quant_platform.simulator.engine import SimulatedHoldingEngine
from quant_platform.simulator.executor import PaperExecutor
from quant_platform.simulator.state import SimState


# ============================================================
# 假数据服务
# ============================================================
class _FakeDataService:
    def __init__(self, quotes: pd.DataFrame, klines: Optional[Dict[str, pd.DataFrame]] = None):
        self._quotes = quotes
        self._klines = klines or {}

    def get_stock_list(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["code", "name", "market"])

    def get_history_data(self, code, start, end, adj="qfq", auto_sync=False):
        return self._klines.get(code, pd.DataFrame())

    def get_realtime_data(self, codes=None):
        df = self._quotes.copy()
        if codes:
            df = df[df["code"].astype(str).str.zfill(6).isin(
                [str(c).zfill(6) for c in codes]
            )]
        return df


def _make_quote(code: str, name: str, last: float, pre_close: float = None) -> dict:
    return {
        "code": code, "name": name,
        "last": last, "open": last, "high": last * 1.02, "low": last * 0.98,
        "pre_close": pre_close if pre_close is not None else last,
        "volume": 100_000, "amount": last * 100_000,
        "turnover_rate": 1.0, "pe_ttm": 15.0, "pb": 2.0, "market_cap": 1e10,
    }


# ============================================================
# BacktestRecordStore
# ============================================================
def test_record_store_save_get(tmp_path: Path):
    store = BacktestRecordStore(tmp_path / "r.db")
    cfg = StrategyConfig(
        name="demo", start_date=date(2025, 1, 1), end_date=date(2025, 6, 1),
        initial_capital=1_000_000, selector=SelectorSpec(conditions=[
            Condition("pe_ttm", "<", 20)
        ]),
    )
    metrics = {"total_return": 0.15, "max_drawdown": -0.05, "sharpe_ratio": 1.2}
    rid = store.save("demo", cfg, metrics, 10, trades=[])
    rec = store.get(rid)
    assert rec is not None
    assert rec.name == "demo"
    assert rec.metrics["total_return"] == 0.15
    assert rec.config.initial_capital == 1_000_000
    assert rec.deployed is False


def test_record_store_mark_deployed(tmp_path: Path):
    store = BacktestRecordStore(tmp_path / "r.db")
    cfg = StrategyConfig(
        name="x", start_date=date(2025,1,1), end_date=date(2025,2,1),
        selector=SelectorSpec(),
    )
    rid = store.save("x", cfg, {"m": 1}, 0)
    store.mark_deployed(rid)
    rec = store.get(rid)
    assert rec.deployed is True
    assert rec.deployed_at is not None


def test_record_store_compare(tmp_path: Path):
    store = BacktestRecordStore(tmp_path / "r.db")
    cfg = StrategyConfig(
        name="cmp", start_date=date(2025,1,1), end_date=date(2025,2,1),
        selector=SelectorSpec(),
    )
    r1 = store.save("cmp", cfg, {"a": 1}, 5)
    r2 = store.save("cmp", cfg, {"a": 2}, 7)
    recs = store.compare([r1, r2])
    assert len(recs) == 2
    assert {r.id for r in recs} == {r1, r2}


def test_record_store_list_recent(tmp_path: Path):
    store = BacktestRecordStore(tmp_path / "r.db")
    cfg = StrategyConfig(
        name="x", start_date=date(2025,1,1), end_date=date(2025,2,1),
        selector=SelectorSpec(),
    )
    for i in range(3):
        store.save(f"x{i}", cfg, {"i": i}, i)
    rows = store.list_recent(10)
    assert len(rows) == 3


# ============================================================
# SimState
# ============================================================
def test_sim_state_crud(tmp_path: Path):
    s = SimState(tmp_path / "sim.db")
    inst_id = s.create_instance(
        "t1", config_json='{"name":"t"}', initial_capital=100_000
    )
    assert s.get_cash(inst_id) == 100_000
    s.set_cash(inst_id, 80_000)
    assert s.get_cash(inst_id) == 80_000

    pos = Position(code="600000", name="A", shares=100, avg_cost=10.0,
                   buy_date=date(2025, 1, 1))
    s.upsert_position(inst_id, pos)
    positions = s.get_positions(inst_id)
    assert len(positions) == 1
    assert positions[0].code == "600000"

    s.add_trade(inst_id, "600000", "A", "buy", 10.0, 100, 1000.0, fee=5.0)
    trades = s.list_trades(inst_id)
    assert len(trades) == 1

    s.save_snapshot(inst_id, date(2025, 1, 2), 80_000, 1_000, 81_000, 0)
    snaps = s.list_snapshots(inst_id)
    assert len(snaps) == 1


def test_sim_state_delete_position(tmp_path: Path):
    s = SimState(tmp_path / "sim.db")
    inst_id = s.create_instance("t", '{}', 100_000)
    s.upsert_position(inst_id, Position(
        code="600000", shares=100, avg_cost=10.0, buy_date=date.today()
    ))
    s.delete_position(inst_id, "600000")
    assert s.get_positions(inst_id) == []


# ============================================================
# PaperExecutor
# ============================================================
def test_executor_buy_basic(tmp_path: Path):
    s = SimState(tmp_path / "sim.db")
    inst = s.create_instance("t", '{}', 100_000)
    exe = PaperExecutor(s, inst)
    r = exe.buy("600000", "A", 10.0, amount=10_000)
    assert r.success
    # 10000//(10*100) = 10 手 = 1000 股
    assert r.shares == 1000
    assert r.shares % 100 == 0
    assert s.get_cash(inst) < 100_000
    assert len(s.get_positions(inst)) == 1


def test_executor_sell_closes_position(tmp_path: Path):
    s = SimState(tmp_path / "sim.db")
    inst = s.create_instance("t", '{}', 100_000)
    exe = PaperExecutor(s, inst)
    exe.buy("600000", "A", 10.0, amount=10_000)
    r = exe.sell("600000", "A", 11.0, reason="test")
    assert r.success
    assert s.get_positions(inst) == []
    assert s.get_cash(inst) > 100_000  # 净赚


def test_executor_buy_merges_position(tmp_path: Path):
    """同一只股票连续买入，股数累加。"""
    s = SimState(tmp_path / "sim.db")
    inst = s.create_instance("t", '{}', 100_000)
    exe = PaperExecutor(s, inst)
    exe.buy("600000", "A", 10.0, amount=5_000)
    exe.buy("600000", "A", 12.0, amount=5_000)
    positions = s.get_positions(inst)
    assert len(positions) == 1
    assert positions[0].shares > 0
    # 加权成本应在 10~12 之间
    assert 10.0 < positions[0].avg_cost < 12.0


def test_executor_buy_insufficient_cash(tmp_path: Path):
    s = SimState(tmp_path / "sim.db")
    inst = s.create_instance("t", '{}', 100)
    exe = PaperExecutor(s, inst)
    r = exe.buy("600000", "A", 10.0, amount=10_000)
    assert not r.success
    assert r.reason == "insufficient_cash"


# ============================================================
# SimulatedHoldingEngine
# ============================================================
def test_engine_from_config_creates_instance(tmp_path: Path):
    s = SimState(tmp_path / "sim.db")
    cfg = StrategyConfig(
        name="t", start_date=date.today(), end_date=date.today() + timedelta(days=30),
        initial_capital=100_000, selector=SelectorSpec(),
    )
    engine = SimulatedHoldingEngine.from_config(cfg, s, name="manual")
    assert engine.instance_id > 0
    assert s.get_cash(engine.instance_id) == 100_000


def test_engine_deploy_from_record(tmp_path: Path):
    store = BacktestRecordStore(tmp_path / "r.db")
    s = SimState(tmp_path / "sim.db")
    cfg = StrategyConfig(
        name="x", start_date=date(2025,1,1), end_date=date(2025,3,1),
        initial_capital=100_000, selector=SelectorSpec(),
    )
    rid = store.save(
        "x", cfg,
        metrics={"final_equity": 115_000, "total_return": 0.15},
        trade_count=10,
    )
    engine = SimulatedHoldingEngine.deploy_from_record(
        record_id=rid, record_store=store, state=s,
    )
    assert engine.instance_id > 0
    # 初始资金 = 回测的 final_equity
    assert s.get_cash(engine.instance_id) == 115_000
    # 回测记录应被标记为已部署
    assert store.get(rid).deployed is True


def test_engine_tick_triggers_stop_loss(tmp_path: Path):
    """构造持续下跌的行情，验证 tick 触发硬止损。"""
    s = SimState(tmp_path / "sim.db")
    cfg = StrategyConfig(
        name="t", start_date=date.today(), end_date=date.today() + timedelta(days=30),
        initial_capital=200_000,
        stop_loss=-0.05,
        take_profit_threshold=10.0,
        take_profit_drawdown=0.10,
        selector=SelectorSpec(),
    )
    engine = SimulatedHoldingEngine.from_config(cfg, s, name="t")
    # 手动买入一只股票
    exe = PaperExecutor(s, engine.instance_id)
    exe.buy("600000", "Test", 100.0, amount=100_000)
    # 行情跌到 -10%
    quotes = pd.DataFrame([_make_quote("600000", "Test", last=90.0, pre_close=100.0)])
    engine.data = _FakeDataService(quotes)
    tick = engine.tick_once()
    actions = tick.last_actions or []
    assert any("stop_loss" in a for a in actions)
    assert len(s.get_positions(engine.instance_id)) == 0


def test_engine_tick_executes_rebalance(tmp_path: Path):
    """调仓日：选股 + 买入。"""
    s = SimState(tmp_path / "sim.db")
    cfg = StrategyConfig(
        name="t",
        start_date=date.today() - timedelta(days=7),
        end_date=date.today() + timedelta(days=30),
        initial_capital=500_000,
        rebalance_freq="daily",  # 强制每个 tick 调仓，便于测试
        max_holdings=2,
        max_buy_per_day=2,
        stop_loss=-0.99,
        take_profit_threshold=10.0,
        selector=SelectorSpec(conditions=[Condition("pe_ttm", "<", 20)]),
    )
    engine = SimulatedHoldingEngine.from_config(cfg, s, name="t")
    quotes = pd.DataFrame([
        _make_quote("600000", "A", last=10.0),
        _make_quote("600001", "B", last=20.0),
    ])
    engine.data = _FakeDataService(quotes)
    # 模拟"周一"调仓：把今天改成周一
    from datetime import datetime as _dt
    today = _dt.now().date()
    while today.weekday() != 0:
        today = today - timedelta(days=1)
    # 直接覆盖 _is_rebalance_day
    orig = engine._is_rebalance_day
    engine._is_rebalance_day = lambda d: True
    try:
        tick = engine.tick_once()
    finally:
        engine._is_rebalance_day = orig
    # 应当有 BUY 操作
    actions = tick.last_actions or []
    assert any("BUY" in a for a in actions)
    assert len(s.get_positions(engine.instance_id)) > 0


def test_engine_state_persists_across_ticks(tmp_path: Path):
    """多次 tick 后的状态应在数据库中累积。"""
    s = SimState(tmp_path / "sim.db")
    cfg = StrategyConfig(
        name="t", start_date=date.today(), end_date=date.today() + timedelta(days=30),
        initial_capital=100_000, selector=SelectorSpec(),
    )
    engine = SimulatedHoldingEngine.from_config(cfg, s, name="t")
    quotes = pd.DataFrame([_make_quote("600000", "A", last=10.0)])
    engine.data = _FakeDataService(quotes)
    for _ in range(3):
        engine.tick_once()
    snaps = s.list_snapshots(engine.instance_id)
    assert len(snaps) == 3
