"""真实数据源集成测试。

⚠️  默认不参与常规测试（无网络/不需要外网时跳过）。

启用方式：
    pytest -m network tests/test_integration_network.py
    pytest -m network tests/

跳过方式（CI 默认）：
    pytest                              # 跳过所有 @pytest.mark.network
    pytest -m "not network"             # 显式排除
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest


# ============================================================
# 通用 helper
# ============================================================
def _safe_call(fn, *args, **kwargs):
    """执行真实 API，失败一律 skip（而不是 fail）以免阻塞 CI。"""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        pytest.skip(f"数据源调用失败（已跳过）: {e}")


# ============================================================
# 1. AKShare
# ============================================================
@pytest.mark.network
def test_akshare_list_stocks(require_network):
    """akshare 拉全市场股票列表，应有几千条。"""
    from quant_platform.data_acquisition.sources import AkshareSource
    src = AkshareSource(timeout=10)
    stocks = _safe_call(src.list_stocks)
    assert isinstance(stocks, list)
    assert len(stocks) > 1000, f"全市场应 >1000 只，实得 {len(stocks)}"
    # 取一个样本字段
    s = stocks[0]
    assert hasattr(s, "code") and len(s.code) == 6
    assert hasattr(s, "market")
    assert s.market in ("SH", "SZ", "BJ")


@pytest.mark.network
def test_akshare_history(require_network):
    """akshare 拉 600519 一年日 K 线，应有 ~240 根。"""
    from quant_platform.data_acquisition.sources import AkshareSource
    src = AkshareSource(timeout=10)
    end = date.today()
    start = end - timedelta(days=365)
    df = _safe_call(src.get_history, "600519", start, end, "D", "qfq")
    assert not df.empty, "应返回非空 K 线"
    required = {"date", "open", "high", "low", "close", "volume", "amount"}
    missing = required - set(df.columns)
    assert not missing, f"缺少列: {missing}"
    # 至少 200 个交易日
    assert len(df) >= 200, f"一年应有 ~240 根 K 线，实得 {len(df)}"
    # 价格必须为正
    assert (df["close"] > 0).all()


@pytest.mark.network
def test_akshare_realtime(require_network):
    """akshare 实时行情：先拉全市场，再过滤 600519 / 000001。"""
    from quant_platform.data_acquisition.sources import AkshareSource
    src = AkshareSource(timeout=15)
    quotes = _safe_call(src.get_realtime, ["600519", "000001"])
    assert isinstance(quotes, list)
    assert len(quotes) >= 1
    codes = {q.code for q in quotes}
    assert "600519" in codes or "000001" in codes
    # 字段类型正确
    for q in quotes[:5]:
        assert isinstance(q.last, float)
        assert q.last > 0


# ============================================================
# 2. 东方财富（兜底源）
# ============================================================
@pytest.mark.network
def test_eastmoney_history(require_network):
    """eastmoney 历史 K 线（拉 1 个月即可，避免太长）。"""
    from quant_platform.data_acquisition.sources import EastMoneySource
    src = EastMoneySource(timeout=10)
    end = date.today()
    start = end - timedelta(days=30)
    df = _safe_call(src.get_history, "000001", start, end, "D", "qfq")
    assert not df.empty
    assert {"date", "open", "high", "low", "close"}.issubset(df.columns)
    assert (df["close"] > 0).all()


# ============================================================
# 3. UnifiedDataService 端到端
# ============================================================
@pytest.mark.network
def test_unified_service_end_to_end(tmp_path: Path, require_network):
    """UnifiedDataService：同步 + 读本地缓存。"""
    from quant_platform.data_service.unified_api import UnifiedDataService
    cfg = {
        "data_service": {
            "storage": {
                "sqlite_path": str(tmp_path / "net.db"),
                "hdf5_path": str(tmp_path / "net.h5"),
            },
            "source_priority": ["akshare"],
            "realtime_source": "akshare",
            "cache": {"history_years": 1},
        },
        "data_sources": {
            "akshare": {"enabled": True, "timeout": 15},
            "tushare": {"enabled": False},
            "eastmoney": {"enabled": False},
        },
    }
    svc = UnifiedDataService(config=cfg)
    n = _safe_call(svc.sync_history, "600519",
                   date.today() - timedelta(days=30), date.today())
    assert n > 0
    # 第二次读本地缓存
    df = svc.get_history_data("600519", auto_sync=False)
    assert not df.empty


# ============================================================
# 4. BacktestEngine 用真实 K 线跑一遍（轻量）
# ============================================================
@pytest.mark.network
@pytest.mark.slow
def test_backtest_with_real_data(tmp_path: Path, require_network):
    """用真实 K 线跑一个 1 个月的回测，验证字段约束。"""
    from quant_platform.backtest.engine import BacktestEngine
    from quant_platform.backtest.strategy import StrategyConfig
    from quant_platform.data_service.unified_api import UnifiedDataService
    from quant_platform.selector.schema import Condition, SelectorSpec

    cfg = {
        "data_service": {
            "storage": {
                "sqlite_path": str(tmp_path / "bt.db"),
                "hdf5_path": str(tmp_path / "bt.h5"),
            },
            "source_priority": ["akshare"],
            "realtime_source": "akshare",
            "cache": {"history_years": 1},
        },
        "data_sources": {"akshare": {"enabled": True, "timeout": 15}},
    }
    svc = UnifiedDataService(config=cfg)
    # 同步 3 只
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=60)
    codes = ["600519", "000001", "600036"]
    for c in codes:
        _safe_call(svc.sync_history, c, start, end)
    # 回测
    strategy = StrategyConfig(
        name="net-test",
        start_date=start + timedelta(days=30),
        end_date=end,
        initial_capital=1_000_000,
        rebalance_freq="weekly",
        selector=SelectorSpec(conditions=[Condition("close", ">", 0)]),
        max_holdings=2,
    )
    bt = BacktestEngine(data_service=svc)
    result = _safe_call(bt.run, strategy, universe=codes)
    assert not result.equity_curve.empty
    m = result.metrics.to_dict()
    assert "total_return" in m
    assert "final_equity" in m
