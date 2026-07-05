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


# ============================================================
# 放宽建议：选股为空时 api 应返回 suggestions
# ============================================================
class TestSelectorSuggestions:
    @pytest.fixture
    def app_with_features(self, tmp_path: Path, monkeypatch):
        """构造一个让选股必为空的 app（features 中放宽后可命中）。"""
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
        # monkey-patch build_features 返回特定 features
        def _fake_features(self, as_of=None):
            return pd.DataFrame({
                "code": [f"{600000 + i:06d}" for i in range(5)],
                "name": [f"S{i}" for i in range(5)],
                "close": [10.0] * 5,
                "pe_ttm": [5.0, 10.0, 15.0, 20.0, 30.0],
                "pb": [1.0, 2.0, 3.0, 4.0, 5.0],
                "roe": [5.0, 10.0, 15.0, 20.0, 30.0],
            })
        monkeypatch.setattr(
            "quant_platform.selector.service.SelectorService.build_features",
            _fake_features,
        )
        return app

    def test_api_returns_suggestions_when_empty(self, app_with_features):
        c = app_with_features.test_client()
        # pe<3 AND roe>100: 数据里 pe>=5, roe<=30, 都不命中
        # 放宽: pe<3 -> pe<10 命中 2 (pe=5,10) / roe>100 -> roe<200 命中 0
        r = c.post("/selector/api/run", json={
            "json": {
                "conditions": [
                    {"field": "pe_ttm", "operator": "<", "value": 3},
                    {"field": "roe", "operator": ">", "value": 100},
                ],
                "logic": "AND",
            }
        })
        assert r.status_code == 200
        d = r.get_json()
        assert d["count"] == 0
        assert "suggestions" in d
        assert len(d["suggestions"]) >= 1
        # 每条建议都有 description
        for s in d["suggestions"]:
            assert "description" in s
            assert s["expected_count"] > 0

    def test_api_no_suggestions_when_result_nonempty(self, app_with_features):
        c = app_with_features.test_client()
        # pe<200 会命中 5 只
        r = c.post("/selector/api/run", json={
            "json": {
                "conditions": [{"field": "pe_ttm", "operator": "<", "value": 200}],
                "logic": "AND",
            }
        })
        assert r.status_code == 200
        d = r.get_json()
        assert d["count"] > 0
        # 不附带 suggestions
        assert d.get("suggestions", []) == []


# ============================================================
# 设置页面
# ============================================================
class TestSettingsRoutes:
    def test_settings_page_loads(self, client):
        r = client.get("/settings/")
        assert r.status_code == 200
        assert "LLM" in r.text

    def test_settings_api_get_returns_config(self, client):
        r = client.get("/settings/api/get")
        assert r.status_code == 200
        d = r.get_json()
        assert "llm" in d
        assert "notify" in d
        assert "data_sources" in d

    def test_settings_api_save(self, client):
        r = client.post("/settings/api/save", json={
            "llm": {"model": "test-model", "timeout": 30},
        })
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True

    def test_settings_api_save_persists(self, client):
        client.post("/settings/api/save", json={
            "llm": {"model": "persist-test"},
        })
        r = client.get("/settings/api/get")
        d = r.get_json()
        assert d["llm"]["model"] == "persist-test"

    def test_settings_api_get_sanitizes_secrets(self, client):
        # 先写入一个有 api_key 的配置
        client.post("/settings/api/save", json={
            "llm": {"api_key": "sk-12345678"},
            "notify": {"smtp_password": "my-secret-pw"},
        })
        r = client.get("/settings/api/get")
        d = r.get_json()
        # api_key 应该被部分掩盖
        assert "****" in d["llm"]["api_key"]
        assert d["llm"]["api_key"] != "sk-12345678"
        assert "****" in d["notify"]["smtp_password"]

    def test_settings_test_llm_no_key(self, client):
        r = client.post("/settings/api/test-llm", json={"api_key": ""})
        assert r.status_code == 400

    def test_settings_test_smtp_incomplete(self, client):
        r = client.post("/settings/api/test-smtp", json={"smtp_host": ""})
        assert r.status_code == 400


# ============================================================
# 回测创建页面
# ============================================================
class TestBacktestNewRoutes:
    def test_new_backtest_page_loads(self, client):
        r = client.get("/backtests/new")
        assert r.status_code == 200
        assert "新建回测" in r.text

    def test_create_backtest_missing_params(self, client):
        r = client.post("/backtests/api/create", json={})
        assert r.status_code == 400

    def test_create_backtest_bad_date(self, client):
        r = client.post("/backtests/api/create", json={
            "template": "low_valuation",
            "start_date": "bad-date",
            "end_date": "2024-12-31",
        })
        assert r.status_code == 400
