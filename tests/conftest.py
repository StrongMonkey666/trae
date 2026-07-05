"""Pytest 共享 fixtures 与配置。

把每个测试都需要的：
- src/ 加入 sys.path
- EventBus 单例重置
- 临时 SQLite/HDF5 目录
- 通用 fake 数据构造器

集中到本文件，避免每个 test_*.py 重复。
"""
from __future__ import annotations

import os
import sys
import socket
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import pytest

# ------------------------------------------------------------
# src 路径
# ------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ------------------------------------------------------------
# 标记注册
# ------------------------------------------------------------
def pytest_configure(config: pytest.Config) -> None:
    """注册自定义 marker，避免出现 'PytestUnknownMarkWarning'。"""
    config.addinivalue_line(
        "markers",
        "network: 需要联网的真实数据源集成测试，默认跳过（用 -m network 启用）",
    )
    config.addinivalue_line(
        "markers",
        "slow: 性能/基准测试（>1s），默认跳过（用 -m slow 启用）",
    )
    config.addinivalue_line(
        "markers",
        "benchmark: 性能基准测试，用于回归",
    )


# ------------------------------------------------------------
# 通用 fixtures
# ------------------------------------------------------------
@pytest.fixture
def tmp_sqlite(tmp_path: Path) -> Path:
    """测试用 SQLite 路径。"""
    return tmp_path / "test.db"


@pytest.fixture
def tmp_hdf5(tmp_path: Path) -> Path:
    """测试用 HDF5 路径。"""
    return tmp_path / "test.h5"


@pytest.fixture
def tmp_config(tmp_sqlite: Path, tmp_hdf5: Path) -> dict:
    """构造一个最小可用的 web 配置 dict。"""
    return {
        "project": {"name": "test"},
        "data_service": {
            "storage": {
                "sqlite_path": str(tmp_sqlite),
                "hdf5_path": str(tmp_hdf5),
            },
            "source_priority": ["fake"],
            "realtime_source": "fake",
        },
        "data_sources": {"fake": {"enabled": True}},
        "logging": {"level": "ERROR"},
    }


@pytest.fixture(autouse=True)
def reset_eventbus():
    """每个测试前重置 EventBus 单例。

    eventbus 的 bus.py 是单例模式，跨测试会污染。
    """
    try:
        from quant_platform.eventbus.bus import EventBus
        EventBus._instance = None
    except Exception:
        pass
    yield
    try:
        from quant_platform.eventbus.bus import EventBus
        EventBus._instance = None
    except Exception:
        pass


# ------------------------------------------------------------
# Fake 数据构造器
# ------------------------------------------------------------
@pytest.fixture
def make_klines():
    """返回 (codes, start, days) -> Dict[code, DataFrame] 形式的 K 线构造器。"""
    def _make(codes, start: date, days: int, base: float = 10.0,
              up_only: bool = True) -> Dict[str, pd.DataFrame]:
        out: Dict[str, pd.DataFrame] = {}
        for i, code in enumerate(codes):
            rows = []
            cur = start
            end = start + timedelta(days=days)
            while cur <= end:
                if cur.weekday() < 5:  # 工作日
                    slope = 0.005 * (i + 1) if up_only else 0.0
                    noise = 0.0 if up_only else (((i % 3) - 1) * 0.01)
                    price = base * (1 + slope * (cur - start).days) + noise
                    rows.append({
                        "date": cur,
                        "open": price,
                        "high": price * 1.02,
                        "low": price * 0.98,
                        "close": price,
                        "volume": 1_000_000,
                        "amount": price * 1_000_000,
                    })
                cur += timedelta(days=1)
            out[code] = pd.DataFrame(rows)
        return out
    return _make


@pytest.fixture
def make_stock_list():
    """返回 (codes, market='SH') -> DataFrame 的股票列表构造器。"""
    def _make(codes, market: str = "SH") -> pd.DataFrame:
        return pd.DataFrame({
            "code": [str(c) for c in codes],
            "name": [f"S{i}" for i in range(len(codes))],
            "market": [market] * len(codes),
        })
    return _make


@pytest.fixture
def fake_data_service(make_klines, make_stock_list):
    """构造一个可注入到 BacktestEngine / SimulatedHoldingEngine 的假数据服务。

    用法：
        svc = fake_data_service(
            codes=["600000", "600001"], start=date(2025,1,1), days=60
        )
        engine = BacktestEngine(data_service=svc)
    """
    def _build(codes, start: date, days: int = 60, base: float = 10.0,
               quotes: Optional[pd.DataFrame] = None):
        klines = make_klines(codes, start, days, base=base)
        stock_list = make_stock_list(codes)

        class _Fake:
            def get_stock_list(self):
                return stock_list

            def get_history_data(self, code, start, end, adj="qfq", auto_sync=False):
                df = klines.get(code)
                if df is None or df.empty:
                    return pd.DataFrame()
                df = df.copy()
                df["date"] = pd.to_datetime(df["date"]).dt.date
                return df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)

            def get_realtime_data(self, codes=None):
                if quotes is None or quotes.empty:
                    return pd.DataFrame()
                if codes:
                    codes6 = [str(c).zfill(6) for c in codes]
                    return quotes[quotes["code"].astype(str).str.zfill(6).isin(codes6)]
                return quotes

        return _Fake()
    return _build


# ------------------------------------------------------------
# 网络可达性探测（用于 skip 网络测试）
# ------------------------------------------------------------
def _tcp_alive(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def network_available() -> bool:
    """会话级 fixture：探测是否能访问 akshare 常用源。

    默认探测东方财富（IP 段稳定，海外可达性较高）。
    """
    # 直接给缓存结果，避免每次重复探测
    if hasattr(network_available, "_cached"):
        return getattr(network_available, "_cached")
    ok = _tcp_alive("82.push2.eastmoney.com", 80, timeout=1.5)
    setattr(network_available, "_cached", ok)
    return ok


@pytest.fixture
def require_network(network_available: bool):
    """在网络测试函数上 fixture 化 skip，避免装饰器样板。"""
    if not network_available:
        pytest.skip("网络不可达，跳过真实数据集成测试")
