"""事件总线测试。"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.eventbus.bus import EventBus, get_bus
from quant_platform.eventbus.events import (
    BacktestCompletedEvent, TradeEvent, SignalEvent, Event,
)
from quant_platform.eventbus.handlers import handler


@pytest.fixture(autouse=True)
def reset_bus():
    """每个测试前重置单例。"""
    EventBus._instance = None
    yield
    EventBus._instance = None


# ============================================================
# 基本订阅 / 发布
# ============================================================
def test_basic_subscribe_publish():
    bus = get_bus()
    received = []
    bus.subscribe("test.event", lambda ev: received.append(ev))
    ev = Event(event_type="test.event", payload={"a": 1})
    n = bus.publish(ev)
    assert n == 1
    assert received[0].payload["a"] == 1


def test_publish_no_handler_returns_zero():
    bus = get_bus()
    n = bus.publish(Event(event_type="never.subscribed"))
    assert n == 0


def test_multiple_handlers_invoked():
    bus = get_bus()
    a, b = [], []
    bus.subscribe("x", lambda ev: a.append(ev))
    bus.subscribe("x", lambda ev: b.append(ev))
    bus.publish(Event(event_type="x"))
    assert len(a) == 1 and len(b) == 1


def test_handler_exception_isolated():
    bus = get_bus()
    good = []

    def bad_handler(ev):
        raise RuntimeError("boom")

    def good_handler(ev):
        good.append(ev)

    bus.subscribe("x", bad_handler)
    bus.subscribe("x", good_handler)
    # 不应抛错
    bus.publish(Event(event_type="x"))
    assert len(good) == 1


# ============================================================
# 模式订阅
# ============================================================
def test_pattern_wildcard_all():
    bus = get_bus()
    received = []
    bus.subscribe_pattern("*", lambda ev: received.append(ev))
    bus.publish(Event(event_type="a.b"))
    bus.publish(Event(event_type="c.d.e"))
    assert len(received) == 2


def test_pattern_prefix():
    bus = get_bus()
    received = []
    bus.subscribe_pattern("backtest.*", lambda ev: received.append(ev))
    bus.publish(Event(event_type="backtest.completed"))
    bus.publish(Event(event_type="trade.executed"))
    assert len(received) == 1
    assert received[0].event_type == "backtest.completed"


def test_pattern_suffix():
    bus = get_bus()
    received = []
    bus.subscribe_pattern("*.completed", lambda ev: received.append(ev))
    bus.publish(Event(event_type="backtest.completed"))
    bus.publish(Event(event_type="selector.completed"))
    bus.publish(Event(event_type="trade.executed"))
    assert len(received) == 2


# ============================================================
# 历史
# ============================================================
def test_history_records_published():
    bus = get_bus()
    for i in range(5):
        bus.publish(Event(event_type="x"))
    h = bus.history()
    assert len(h) == 5


def test_history_filtered_by_type():
    bus = get_bus()
    bus.publish(Event(event_type="a"))
    bus.publish(Event(event_type="b"))
    bus.publish(Event(event_type="a"))
    h = bus.history(event_type="a")
    assert len(h) == 2
    assert all(e.event_type == "a" for e in h)


def test_history_max_size():
    bus = get_bus()
    bus._max_history = 10
    for _ in range(20):
        bus.publish(Event(event_type="x"))
    assert len(bus._history) == 10


# ============================================================
# 装饰器
# ============================================================
def test_decorator_registers_handler():
    bus = get_bus()
    received = []

    @handler("custom.event", bus=bus)
    def on_event(ev):
        received.append(ev)

    bus.publish(Event(event_type="custom.event"))
    assert len(received) == 1
    assert on_event.__event_type__ == "custom.event"


# ============================================================
# 预置事件类型
# ============================================================
def test_backtest_event_to_dict():
    ev = BacktestCompletedEvent(
        record_id=1, name="test",
        metrics={"total_return": 0.15},
    )
    d = ev.to_dict()
    assert d["event_type"] == "backtest.completed"
    assert d["record_id"] == 1
    assert d["metrics"]["total_return"] == 0.15


def test_trade_event_to_dict():
    ev = TradeEvent(
        instance_id=1, code="600000", name="A",
        side="buy", price=10.0, shares=100, amount=1000.0,
    )
    d = ev.to_dict()
    assert d["code"] == "600000"
    assert d["side"] == "buy"


def test_signal_event_to_dict():
    ev = SignalEvent(
        instance_id=1, code="600000", signal="stop_loss",
    )
    d = ev.to_dict()
    assert d["signal"] == "stop_loss"


# ============================================================
# 单例
# ============================================================
def test_singleton():
    b1 = EventBus()
    b2 = EventBus()
    assert b1 is b2
    assert b1 is get_bus()


# ============================================================
# 线程安全
# ============================================================
def test_concurrent_publish():
    bus = get_bus()
    counter = [0]
    lock = threading.Lock()

    def increment(ev):
        with lock:
            counter[0] += 1

    bus.subscribe("e", increment)
    threads = []
    for _ in range(8):
        t = threading.Thread(target=lambda: [
            bus.publish(Event(event_type="e")) for _ in range(50)
        ])
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    assert counter[0] == 400
