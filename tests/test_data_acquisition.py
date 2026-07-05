"""数据获取系统测试。

涵盖：
- 数据源基类接口
- 存储层（SQLite + HDF5）
- 数据清洗
- 统一服务降级
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.data_acquisition.cleaner import (
    apply_adjustment,
    clean_history,
    fill_missing,
    mark_suspended,
    merge_incremental,
    normalize_columns,
)
from quant_platform.data_acquisition.sources.base import (
    DataSourceBase,
    FinancialIndicator,
    KLineBar,
    Quote,
    StockInfo,
)
from quant_platform.data_service.storage import DataStore, Hdf5Store, SqliteStore
from quant_platform.utils.trading_calendar import TradingCalendar


# ============================================================
# 数据源抽象基类
# ============================================================
class _FakeSource(DataSourceBase):
    name = "fake"

    def __init__(self, fail: bool = False):
        super().__init__(timeout=1)
        self.fail = fail

    def list_stocks(self):
        if self.fail:
            raise RuntimeError("boom")
        return [StockInfo(code="600519", name="贵州茅台", market="SH")]

    def get_realtime(self, codes):
        if self.fail:
            raise RuntimeError("boom")
        return [
            Quote(
                code="600519", name="贵州茅台",
                last=1800.0, open=1780.0, high=1810.0, low=1770.0,
                pre_close=1790.0, volume=10000, amount=1.8e9,
            )
        ]

    def get_history(self, code, start, end, freq="D", adj="qfq"):
        if self.fail:
            raise RuntimeError("boom")
        return pd.DataFrame({
            "date": [start, end],
            "open": [100.0, 102.0],
            "close": [101.0, 103.0],
            "high": [102.0, 104.0],
            "low": [99.0, 101.0],
            "volume": [1000, 1500],
            "amount": [1e5, 1.5e5],
            "adj_factor": [1.0, 1.0],
        })

    def get_financial(self, code, start, end):
        if self.fail:
            raise RuntimeError("boom")
        return [
            FinancialIndicator(
                code=code, report_date=end, eps=10.0, roe=0.2, revenue=1e9,
            )
        ]


def test_datasource_base_contract():
    src = _FakeSource()
    assert src.ping() is True
    stocks = src.list_stocks()
    assert stocks[0].code == "600519"
    quotes = src.get_realtime(["600519"])
    assert quotes[0].last == 1800.0
    df = src.get_history("600519", date(2025, 1, 1), date(2025, 1, 2))
    assert "close" in df.columns
    fin = src.get_financial("600519", date(2024, 1, 1), date(2025, 1, 1))
    assert fin[0].eps == 10.0


def test_datasource_ping_failure():
    assert _FakeSource(fail=True).ping() is False


# ============================================================
# 清洗模块
# ============================================================
def test_cleaner_normalize_columns():
    df = pd.DataFrame({"日期": [date(2025, 1, 1)], "open": [10.0]})
    out = normalize_columns(df)
    assert list(out.columns) == [
        "date", "open", "high", "low", "close", "volume", "amount", "adj_factor"
    ]


def test_cleaner_fill_missing_marks_suspended():
    raw = pd.DataFrame({
        "date": [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)],
        "open": [10.0, 10.0, 11.0],
        "high": [10.5, 10.5, 11.5],
        "low": [9.5, 9.5, 10.5],
        "close": [10.2, 10.2, 11.2],
        "volume": [100, 0, 150],
        "amount": [1000, 0, 1500],
        "adj_factor": [1.0, 1.0, 1.0],
    })
    out = clean_history(raw, adj="qfq")
    assert "suspended" in out.columns
    # 第二行 volume=0 且价格与前日相同 -> 标记为停牌
    assert bool(out.iloc[1]["suspended"]) is True
    assert bool(out.iloc[0]["suspended"]) is False


def test_cleaner_apply_adjustment_hfq():
    raw = pd.DataFrame({
        "date": [date(2025, 1, 1)],
        "open": [10.0], "high": [10.5], "low": [9.5], "close": [10.2],
        "volume": [100], "amount": [1000], "adj_factor": [2.0],
    })
    out = apply_adjustment(raw.copy(), adj="hfq")
    assert float(out["close"].iloc[0]) == pytest.approx(20.4)
    assert "adj_factor" not in out.columns


def test_cleaner_merge_incremental():
    a = pd.DataFrame({
        "date": [date(2025, 1, 1)], "open": [1.0], "close": [1.0],
        "high": [1.0], "low": [1.0], "volume": [10], "amount": [100],
        "adj_factor": [1.0],
    })
    b = pd.DataFrame({
        "date": [date(2025, 1, 1), date(2025, 1, 2)],
        "open": [1.1, 1.2], "close": [1.1, 1.2],
        "high": [1.1, 1.2], "low": [1.1, 1.2],
        "volume": [20, 30], "amount": [200, 300], "adj_factor": [1.0, 1.0],
    })
    merged = merge_incremental(a, b)
    assert len(merged) == 2
    assert float(merged.iloc[0]["open"]) == 1.1  # 同日期被覆盖


# ============================================================
# 交易日历
# ============================================================
def test_trading_calendar_weekend():
    cal = TradingCalendar()
    assert cal.is_trading_day(date(2025, 1, 3)) is True   # 周五
    assert cal.is_trading_day(date(2025, 1, 4)) is False  # 周六
    assert cal.is_trading_day(date(2025, 1, 5)) is False  # 周日


def test_trading_calendar_holiday():
    cal = TradingCalendar(holidays=["2025-10-01", "2025-10-02"])
    assert cal.is_trading_day(date(2025, 10, 1)) is False
    assert cal.is_trading_day(date(2025, 10, 3)) is True


# ============================================================
# 存储层
# ============================================================
def test_sqlite_store_basic(tmp_path: Path):
    store = SqliteStore(tmp_path / "t.db")
    store.upsert_stocks([
        {"code": "600519", "name": "贵州茅台", "market": "SH", "industry": "白酒"},
        {"code": "000001", "name": "平安银行", "market": "SZ"},
    ])
    df = store.list_stocks()
    assert len(df) == 2
    one = store.get_stock("600519")
    assert one["name"] == "贵州茅台"
    store.update_source_status("akshare", ok=True, note="ok")
    store.log_sync("akshare", "D", date(2025, 1, 1), date(2025, 1, 2), 10, True, code="600519")
    status = store.source_status()
    assert "akshare" in set(status["name"])


def test_hdf5_store_roundtrip(tmp_path: Path):
    store = Hdf5Store(tmp_path / "t.h5")
    df = pd.DataFrame({
        "date": [date(2025, 1, 1), date(2025, 1, 2)],
        "open": [1.0, 1.1], "close": [1.05, 1.15],
        "high": [1.1, 1.2], "low": [0.9, 1.0],
        "volume": [100, 200], "amount": [1000, 2000],
        "adj_factor": [1.0, 1.0],
    })
    store.save_kline("600519", df)
    assert store.has_kline("600519")
    out = store.load_kline("600519")
    assert len(out) == 2
    assert "600519" in store.list_kline_codes()

    # 增量更新
    new = pd.DataFrame({
        "date": [date(2025, 1, 2), date(2025, 1, 3)],
        "open": [1.11, 1.21], "close": [1.16, 1.26],
        "high": [1.22, 1.32], "low": [1.01, 1.11],
        "volume": [300, 400], "amount": [3000, 4000],
        "adj_factor": [1.0, 1.0],
    })
    store.save_kline("600519", new)
    out2 = store.load_kline("600519")
    assert len(out2) == 3
    assert float(out2[out2["date"] == date(2025, 1, 2)]["close"].iloc[0]) == 1.16


# ============================================================
# 统一服务（不依赖外网）
# ============================================================
def test_unified_service_fallback(monkeypatch, tmp_path: Path):
    """主源失败时应降级到备用源。"""
    from quant_platform.data_service import unified_api

    # 构造一个临时数据目录
    monkeypatch.setitem(
        unified_api.__dict__,
        "_TMP",
        str(tmp_path),
    )

    # 直接使用 build_sources + DataStore 拼装
    cfg = {
        "data_service": {
            "storage": {
                "sqlite_path": str(tmp_path / "q.db"),
                "hdf5_path": str(tmp_path / "q.h5"),
            },
            "source_priority": ["fake1", "fake2"],
            "realtime_source": "fake1",
        },
        "data_sources": {
            "fake1": {"enabled": True},
            "fake2": {"enabled": True},
        },
    }
    service = unified_api.UnifiedDataService(config=cfg)
    service.sources = {
        "fake1": _FakeSource(fail=True),
        "fake2": _FakeSource(fail=False),
    }
    df = service.get_history_data("600519", start=date(2025, 1, 1), end=date(2025, 1, 2))
    assert not df.empty
    assert len(df) == 2


def test_unified_service_all_fail(monkeypatch, tmp_path: Path):
    """全部数据源失败时不抛异常，仅返回空。"""
    from quant_platform.data_service import unified_api

    cfg = {
        "data_service": {
            "storage": {
                "sqlite_path": str(tmp_path / "q.db"),
                "hdf5_path": str(tmp_path / "q.h5"),
            },
            "source_priority": ["fake1"],
            "realtime_source": "fake1",
        },
        "data_sources": {"fake1": {"enabled": True}},
    }
    service = unified_api.UnifiedDataService(config=cfg)
    service.sources = {"fake1": _FakeSource(fail=True)}
    # 同步时所有源失败 -> 返回 0
    assert service.sync_history("600519", date(2025, 1, 1), date(2025, 1, 2)) == 0
