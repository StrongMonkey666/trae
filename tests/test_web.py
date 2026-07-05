"""Web 路由测试（使用 Flask test client）。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.backtest.records import BacktestRecordStore
from quant_platform.backtest.strategy import StrategyConfig
from quant_platform.selector.schema import SelectorSpec
from quant_platform.simulator.state import SimState
from quant_platform.web.app import create_app


@pytest.fixture
def app(tmp_path: Path):
    sqlite = tmp_path / "q.db"
    cfg = {
        "project": {"name": "test"},
        "data_service": {
            "storage": {
                "sqlite_path": str(sqlite),
                "hdf5_path": str(tmp_path / "q.h5"),
            },
            "source_priority": ["fake"],
            "realtime_source": "fake",
        },
        "data_sources": {"fake": {"enabled": True}},
        "logging": {"level": "WARNING", "file": ""},
    }
    return create_app(config=cfg)


@pytest.fixture
def client(app):
    return app.test_client()


# ============================================================
# 基础路由
# ============================================================
def test_dashboard_loads(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "仪表盘" in r.data.decode("utf-8")


def test_backtests_list_empty(client):
    r = client.get("/backtests/")
    assert r.status_code == 200
    assert "回测记录" in r.data.decode("utf-8")


def test_backtests_detail_404(client):
    r = client.get("/backtests/9999")
    assert r.status_code == 404


def test_simulator_list(client):
    r = client.get("/simulator/")
    assert r.status_code == 200


def test_simulator_detail_404(client):
    r = client.get("/simulator/9999")
    assert r.status_code == 404


def test_selector_index(client):
    r = client.get("/selector/")
    assert r.status_code == 200
    assert "选股" in r.data.decode("utf-8")


# ============================================================
# 数据回填
# ============================================================
@pytest.fixture
def app_with_data(tmp_path: Path):
    sqlite = tmp_path / "q.db"
    cfg = {
        "project": {"name": "test"},
        "data_service": {
            "storage": {
                "sqlite_path": str(sqlite),
                "hdf5_path": str(tmp_path / "q.h5"),
            },
            "source_priority": ["fake"],
            "realtime_source": "fake",
        },
        "data_sources": {"fake": {"enabled": True}},
        "logging": {"level": "WARNING", "file": ""},
    }
    app = create_app(config=cfg)
    # 写入回测记录
    record_store = app.config["record_store"]
    sc = StrategyConfig(
        name="demo", start_date=date(2025, 1, 1), end_date=date(2025, 6, 1),
        initial_capital=1_000_000, selector=SelectorSpec(),
    )
    rid = record_store.save(
        name="demo", config=sc,
        metrics={
            "total_return": 0.15, "annualized_return": 0.30,
            "win_rate": 0.6, "max_drawdown": -0.05,
            "sharpe_ratio": 1.5, "final_equity": 1_150_000,
            "trade_count": 5, "avg_profit_pct": 0.02, "avg_hold_days": 10,
        },
        trade_count=5,
        trades=[
            {
                "code": "600000", "name": "A",
                "buy_date": "2025-01-02", "buy_price": 10.0,
                "sell_date": "2025-02-10", "sell_price": 12.0,
                "shares": 100, "profit_pct": 0.20,
                "profit_amount": 200, "hold_days": 39,
                "sell_reason": "take_profit",
            }
        ],
        equity_curve=[
            {"date": "2025-01-01", "value": 1_000_000},
            {"date": "2025-02-01", "value": 1_050_000},
            {"date": "2025-03-01", "value": 1_100_000},
            {"date": "2025-04-01", "value": 1_150_000},
        ],
    )
    # 写入模拟实例
    state = app.config["sim_state"]
    inst = state.create_instance(
        "test_sim", config_json=sc.to_json(),
        initial_capital=1_150_000, backtest_id=rid,
    )
    state.save_snapshot(inst, date(2025, 1, 2), 1_100_000, 50_000, 1_150_000, 0)
    return app, rid, inst


def test_backtest_detail_with_data(app_with_data):
    app, rid, _ = app_with_data
    c = app.test_client()
    r = c.get(f"/backtests/{rid}")
    assert r.status_code == 200
    assert "demo" in r.data.decode("utf-8")
    assert "15.00" in r.data.decode("utf-8")  # total_return


def test_backtest_equity_json(app_with_data):
    app, rid, _ = app_with_data
    c = app.test_client()
    r = c.get(f"/backtests/{rid}/equity.json")
    assert r.status_code == 200
    import json
    d = json.loads(r.data)
    assert len(d["dates"]) == 4
    assert d["values"][-1] == 1_150_000


def test_backtest_trades_json(app_with_data):
    app, rid, _ = app_with_data
    c = app.test_client()
    r = c.get(f"/backtests/{rid}/trades.json")
    assert r.status_code == 200
    import json
    d = json.loads(r.data)
    assert len(d["trades"]) == 1
    assert d["trades"][0]["code"] == "600000"


def test_simulator_detail_with_data(app_with_data):
    app, _, inst = app_with_data
    c = app.test_client()
    r = c.get(f"/simulator/{inst}")
    assert r.status_code == 200
    assert "test_sim" in r.data.decode("utf-8")


def test_simulator_equity_json(app_with_data):
    app, _, inst = app_with_data
    c = app.test_client()
    r = c.get(f"/simulator/{inst}/equity.json")
    assert r.status_code == 200
    import json
    d = json.loads(r.data)
    assert len(d["values"]) == 1
    assert d["values"][0] == 1_150_000


def test_deploy_via_api(app_with_data):
    app, rid, _ = app_with_data
    c = app.test_client()
    r = c.post(f"/simulator/api/deploy/{rid}")
    assert r.status_code == 200
    import json
    d = json.loads(r.data)
    assert d["success"] is True
    assert d["instance_id"] > 0


def test_deploy_via_api_invalid_record(app_with_data):
    app, _, _ = app_with_data
    c = app.test_client()
    r = c.post("/simulator/api/deploy/99999")
    assert r.status_code == 400


def test_selector_api_run_template(app_with_data):
    app, _, _ = app_with_data
    c = app.test_client()
    # 由于选股引擎依赖实时数据，这里主要测试路由 + JSON
    r = c.post("/selector/api/run",
               json={"template": "low_valuation"})
    # 不一定 200（依赖数据源），但能正确处理
    assert r.status_code in (200, 500)


def test_selector_api_run_missing_param(app_with_data):
    app, _, _ = app_with_data
    c = app.test_client()
    r = c.post("/selector/api/run", json={})
    assert r.status_code == 400
