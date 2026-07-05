"""端到端冒烟测试脚本。

用 fake 数据贯通整个流程：选股 -> 回测 -> 入库 -> 部署模拟 -> 事件发布。
不依赖任何外网，可在任何环境跑。

用法：
    python scripts/smoke_test.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.backtest.engine import BacktestEngine
from quant_platform.backtest.records import BacktestRecordStore
from quant_platform.backtest.strategy import StrategyConfig
from quant_platform.eventbus.bus import get_bus
from quant_platform.eventbus.events import Event
from quant_platform.selector.schema import Condition, SelectorSpec
from quant_platform.simulator.engine import SimulatedHoldingEngine
from quant_platform.simulator.state import SimState
from quant_platform.utils.logger import get_logger, setup_logging


# ============================================================
# 假数据
# ============================================================
def _make_klines(codes, start, days, base=10.0):
    """生成单调上涨 K 线（便于选股条件过滤）。"""
    out = {}
    for i, code in enumerate(codes):
        rows = []
        cur = start
        end = start + timedelta(days=days)
        while cur <= end:
            if cur.weekday() < 5:
                slope = 0.005 if i % 2 == 0 else 0.002  # 偶数股涨更多
                price = base * (1 + slope * (cur - start).days)
                rows.append({
                    "date": cur,
                    "open": price, "high": price * 1.02,
                    "low": price * 0.98, "close": price,
                    "volume": 1_000_000, "amount": price * 1_000_000,
                })
            cur += timedelta(days=1)
        out[code] = pd.DataFrame(rows)
    return out


class _FakeDataService:
    def __init__(self, klines, stock_list, quotes=None):
        self._klines = klines
        self._stock_list = stock_list
        self._quotes = quotes

    def get_stock_list(self) -> pd.DataFrame:
        return self._stock_list

    def get_history_data(self, code, start, end, adj="qfq", auto_sync=False):
        df = self._klines.get(code)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)

    def get_realtime_data(self, codes=None):
        if self._quotes is None or self._quotes.empty:
            return pd.DataFrame()
        if codes:
            codes6 = [str(c).zfill(6) for c in codes]
            return self._quotes[self._quotes["code"].astype(str).str.zfill(6).isin(codes6)]
        return self._quotes


# ============================================================
# 主流程
# ============================================================
def main() -> int:
    setup_logging(level="WARNING", log_file=None)
    log = get_logger("smoke")
    failed = []

    def _check(name, cond, detail=""):
        status = "✓" if cond else "✗"
        print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            failed.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        sqlite = Path(tmp) / "smoke.db"
        print("=" * 60)
        print("Quant Platform V1.0 冒烟测试")
        print("=" * 60)

        # ---------- 1. 选股 ----------
        print("\n[1] 选股引擎")
        from quant_platform.selector.engine import SelectorEngine
        engine_sel = SelectorEngine()
        features = pd.DataFrame({
            "code": ["600000", "600001", "600002"],
            "name": ["A", "B", "C"],
            "pe_ttm": [10, 30, 15],
            "roe": [12, 5, 20],
        })
        spec = SelectorSpec(
            conditions=[Condition("pe_ttm", "<", 20), Condition("roe", ">", 10)],
            logic="AND", sort_by="pe_ttm", sort_order="asc", limit=2,
        )
        result = engine_sel.run(spec, features)
        _check("选股过滤+排序+截断", list(result["code"]) == ["600000", "600002"])

        # ---------- 2. 回测 ----------
        print("\n[2] 回测引擎")
        start = date(2025, 1, 6)
        end = date(2025, 2, 28)
        codes = ["600000", "600001", "600002", "600003", "600004"]
        klines = _make_klines(codes, start - timedelta(days=30), 60)
        cfg = StrategyConfig(
            name="smoke", start_date=start, end_date=end,
            initial_capital=1_000_000, rebalance_freq="weekly",
            selector=SelectorSpec(conditions=[Condition("close", ">", 0)]),
            max_holdings=2, max_buy_per_day=1, max_sell_per_day=5,
            stop_loss=-0.50, take_profit_threshold=10.0,
        )
        stock_list = pd.DataFrame({"code": codes, "name": [f"S{i}" for i in range(5)], "market": ["SH"]*5})
        svc = _FakeDataService(klines, stock_list)
        bt_result = BacktestEngine(data_service=svc).run(cfg, universe=codes)
        _check("回测产生权益曲线", not bt_result.equity_curve.empty)
        _check("回测产生交易", len(bt_result.trades) >= 0)
        _check("回测计算指标", "total_return" in bt_result.metrics.to_dict())

        # ---------- 3. 回测记录入库 + 事件 ----------
        print("\n[3] 回测记录 + 事件总线")
        bus = get_bus()
        received = []
        bus.subscribe("backtest.completed", lambda ev: received.append(ev))

        store = BacktestRecordStore(sqlite)
        rid = store.save(
            cfg.name, cfg, bt_result.metrics.to_dict(),
            len(bt_result.trades),
            trades=[t.to_dict() for t in bt_result.trades],
            equity_curve=[
                {"date": str(d.date()), "value": float(v)}
                for d, v in zip(bt_result.equity_curve["date"], bt_result.equity_curve["value"])
            ],
        )
        _check("回测记录入库", rid > 0)
        bus.publish(Event(
            event_type="backtest.completed",
            source="smoke",
            payload={"name": cfg.name, "record_id": rid, "metrics": bt_result.metrics.to_dict()},
        ))
        _check("事件被订阅", len(received) == 1)

        # ---------- 4. 一键部署 ----------
        print("\n[4] 一键部署")
        state = SimState(sqlite)
        deployed = []
        bus.subscribe("simulator.deployed", lambda ev: deployed.append(ev))
        sim = SimulatedHoldingEngine.deploy_from_record(
            record_id=rid, record_store=store, state=state,
        )
        _check("创建模拟实例", sim.instance_id > 0)
        _check("初始资金 = 回测 final_equity",
               abs(state.get_cash(sim.instance_id) - bt_result.metrics.final_equity) < 1)
        _check("发布部署事件", len(deployed) == 1)
        _check("回测标记为已部署", store.get(rid).deployed)

        # ---------- 5. 模拟 tick ----------
        print("\n[5] 模拟引擎")
        # 构造实时行情
        quotes = pd.DataFrame([{
            "code": "600000", "name": "A", "last": 12.0, "open": 11.5,
            "high": 12.5, "low": 11.0, "pre_close": 11.0,
            "volume": 1_000_000, "amount": 1.2e7,
            "turnover_rate": 1.0, "pe_ttm": 10.0, "pb": 2.0, "market_cap": 1e10,
        }])
        sim.data = _FakeDataService(klines, stock_list, quotes=quotes)
        tick = sim.tick_once()
        _check("tick 返回结果", tick.timestamp is not None)
        _check("tick 写快照", len(state.list_snapshots(sim.instance_id)) == 1)

        # ---------- 6. 事件订阅 + 邮件 ----------
        print("\n[6] 邮件通知（mock SMTP）")
        from unittest.mock import MagicMock
        from quant_platform.notify.notifier import Notifier
        smtp = MagicMock()
        notifier = Notifier(
            smtp=smtp, to_addrs=["test@example.com"], bus=bus,
            event_types=["backtest.completed", "trade.executed"],
        )
        bus.publish(Event(
            event_type="backtest.completed",
            source="smoke",
            payload={"name": "test", "record_id": 99, "metrics": {}},
        ))
        _check("邮件已发送", smtp.send.call_count >= 1)
        notifier.shutdown()

        # ---------- 7. Web 路由 ----------
        print("\n[7] Web 路由")
        from quant_platform.web.app import create_app
        web_cfg = {
            "project": {"name": "smoke"},
            "data_service": {
                "storage": {
                    "sqlite_path": str(sqlite),
                    "hdf5_path": str(Path(tmp) / "x.h5"),
                },
                "source_priority": ["fake"],
                "realtime_source": "fake",
            },
            "data_sources": {"fake": {"enabled": True}},
            "logging": {"level": "ERROR"},
        }
        app = create_app(config=web_cfg)
        c = app.test_client()
        r = c.get("/")
        _check("GET /", r.status_code == 200)
        _check("GET /backtests/", c.get("/backtests/").status_code == 200)
        _check("GET /simulator/", c.get("/simulator/").status_code == 200)
        _check("GET /selector/", c.get("/selector/").status_code == 200)
        _check(f"GET /backtests/{rid}", c.get(f"/backtests/{rid}").status_code == 200)
        _check(f"GET /simulator/{sim.instance_id}", c.get(f"/simulator/{sim.instance_id}").status_code == 200)
        eq = c.get(f"/backtests/{rid}/equity.json")
        _check("权益 JSON 可用", eq.status_code == 200 and len(eq.json["dates"]) > 0)

    # ---------- 总结 ----------
    print("\n" + "=" * 60)
    if failed:
        print(f"✗ 冒烟测试失败 {len(failed)} 项:")
        for f in failed:
            print(f"  - {f}")
        return 1
    print("✓ 所有冒烟测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
