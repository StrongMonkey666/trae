"""邮件通知测试。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.eventbus.bus import EventBus
from quant_platform.eventbus.events import (
    BacktestCompletedEvent, TradeEvent, SignalEvent, Event,
)
from quant_platform.notify.email import SmtpClient, SmtpConfig
from quant_platform.notify.notifier import (
    Notifier, render_backtest_completed, render_trade, render_signal,
    build_default_notifier,
)


@pytest.fixture(autouse=True)
def reset_bus():
    EventBus._instance = None
    yield
    EventBus._instance = None


# ============================================================
# SmtpClient
# ============================================================
def test_smtp_config_defaults():
    cfg = SmtpConfig(host="smtp.qq.com", username="u", password="p")
    assert cfg.port == 465
    assert cfg.use_ssl is True


def test_smtp_client_sets_from_addr_from_username():
    cfg = SmtpConfig(host="smtp.qq.com", username="u", password="p")
    client = SmtpClient(cfg)
    assert client.config.from_addr == "u"


@patch("smtplib.SMTP_SSL")
def test_smtp_send_success(mock_smtp_ssl):
    mock_server = MagicMock()
    mock_smtp_ssl.return_value.__enter__.return_value = mock_server
    cfg = SmtpConfig(host="smtp.qq.com", username="u", password="p")
    client = SmtpClient(cfg)
    ok = client.send("subject", "body", ["to@example.com"])
    assert ok is True
    mock_server.login.assert_called_once_with("u", "p")
    mock_server.sendmail.assert_called_once()


def test_smtp_send_no_host_skips():
    cfg = SmtpConfig(host="", username="u")
    client = SmtpClient(cfg)
    ok = client.send("s", "b", ["a@b.com"])
    assert ok is False


def test_smtp_send_no_recipients_skips():
    cfg = SmtpConfig(host="smtp.qq.com", username="u")
    client = SmtpClient(cfg)
    ok = client.send("s", "b", [])
    assert ok is False


# ============================================================
# 渲染器
# ============================================================
def test_render_backtest_completed():
    ev = Event(
        event_type="backtest.completed",
        payload={
            "name": "low_value",
            "record_id": 1,
            "metrics": {
                "total_return": 0.15, "annualized_return": 0.10,
                "max_drawdown": -0.05, "win_rate": 0.6,
                "sharpe_ratio": 1.5, "trade_count": 30,
                "final_equity": 1150000.0,
            },
        },
    )
    msg = render_backtest_completed(ev)
    assert "low_value" in msg["subject"]
    assert "15.00" in msg["text"]  # 收益率 * 100
    assert "1150000" in msg["text"]


def test_render_trade():
    ev = Event(
        event_type="trade.executed",
        payload={
            "instance_id": 1, "code": "600000", "name": "A",
            "side": "buy", "price": 10.0, "shares": 100,
            "amount": 1000.0, "reason": "rebalance",
        },
    )
    msg = render_trade(ev)
    assert "600000" in msg["subject"]
    assert "买入" in msg["subject"]


def test_render_signal():
    ev = Event(
        event_type="signal.triggered",
        payload={
            "instance_id": 1, "code": "600000", "name": "A",
            "signal": "stop_loss", "profit_pct": -0.10,
        },
    )
    msg = render_signal(ev)
    assert "硬止损" in msg["subject"]


# ============================================================
# Notifier
# ============================================================
def test_notifier_subscribes_and_sends():
    bus = EventBus()
    sent = []
    smtp = MagicMock()
    smtp.send = MagicMock(side_effect=lambda **kw: sent.append(kw))
    notifier = Notifier(
        smtp=smtp, to_addrs=["a@b.com"], bus=bus,
        event_types=["backtest.completed", "trade.executed"],
    )
    bus.publish(Event(
        event_type="backtest.completed",
        payload={"name": "x", "record_id": 1, "metrics": {}},
    ))
    bus.publish(Event(
        event_type="trade.executed",
        payload={
            "instance_id": 1, "code": "600000", "name": "A",
            "side": "buy", "price": 10.0, "shares": 100,
            "amount": 1000.0, "reason": "r",
        },
    ))
    assert len(sent) == 2
    notifier.shutdown()


def test_notifier_swallows_send_error():
    bus = EventBus()
    smtp = MagicMock()
    smtp.send = MagicMock(side_effect=Exception("smtp down"))
    notifier = Notifier(
        smtp=smtp, to_addrs=["a@b.com"], bus=bus,
    )
    # 不应抛错
    bus.publish(Event(
        event_type="backtest.completed",
        payload={"name": "x", "record_id": 1, "metrics": {}},
    ))
    notifier.shutdown()


def test_notifier_no_smtp_does_nothing():
    bus = EventBus()
    notifier = Notifier(smtp=None, to_addrs=[], bus=bus)
    bus.publish(Event(event_type="backtest.completed", payload={}))
    notifier.shutdown()


def test_build_default_notifier_disabled():
    assert build_default_notifier({"notify": {"enabled": False}}) is None


def test_build_default_notifier_incomplete():
    n = build_default_notifier({
        "notify": {"enabled": True, "smtp_host": "", "to_addrs": ""}
    })
    assert n is None


def test_build_default_notifier_full():
    n = build_default_notifier({
        "notify": {
            "enabled": True,
            "smtp_host": "smtp.qq.com",
            "smtp_port": 465,
            "smtp_user": "u",
            "smtp_password": "p",
            "to_addrs": "a@b.com,c@d.com",
        }
    })
    assert n is not None
    n.shutdown()
