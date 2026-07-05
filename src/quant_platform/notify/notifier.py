"""通知管理器：订阅事件总线，把关键事件转为邮件。

事件 -> 邮件模板 映射：
- backtest.completed       -> 回测完成报告
- selector.completed       -> 选股结果摘要
- trade.executed           -> 成交通知
- signal.triggered         -> 止盈/止损等信号触发
- simulator.deployed       -> 部署成功
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..eventbus.bus import EventBus, get_bus
from ..eventbus.events import Event
from ..eventbus.handlers import handler
from ..utils.logger import get_logger
from .email import SmtpClient, SmtpConfig

logger = get_logger(__name__)


# ============================================================
# 邮件模板
# ============================================================
def _fmt_pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def render_backtest_completed(ev: Event) -> Dict[str, str]:
    m = ev.payload.get("metrics", {})
    subject = f"[回测完成] {ev.payload.get('name', 'unknown')}"
    lines = [
        f"回测名称: {ev.payload.get('name')}",
        f"回测 ID:  {ev.payload.get('record_id')}",
        "",
        "绩效指标：",
        f"  总收益率:   {m.get('total_return', 0) * 100:.2f}%",
        f"  年化收益:   {m.get('annualized_return', 0) * 100:.2f}%",
        f"  最大回撤:   {m.get('max_drawdown', 0) * 100:.2f}%",
        f"  胜率:       {m.get('win_rate', 0) * 100:.2f}%",
        f"  夏普比率:   {m.get('sharpe_ratio', 0):.2f}",
        f"  交易笔数:   {m.get('trade_count', 0)}",
        f"  最终权益:   {m.get('final_equity', 0):.2f}",
    ]
    return {"subject": subject, "text": "\n".join(lines)}


def render_trade(ev: Event) -> Dict[str, str]:
    side = ev.payload.get("side", "")
    side_cn = "买入" if side == "buy" else "卖出"
    subject = f"[{side_cn}成交] {ev.payload.get('code')} {ev.payload.get('name', '')}"
    lines = [
        f"实例:     #{ev.payload.get('instance_id')}",
        f"标的:     {ev.payload.get('code')} {ev.payload.get('name', '')}",
        f"方向:     {side_cn}",
        f"价格:     {ev.payload.get('price', 0):.2f}",
        f"股数:     {ev.payload.get('shares', 0)}",
        f"金额:     {ev.payload.get('amount', 0):.2f}",
        f"原因:     {ev.payload.get('reason', '')}",
        f"时间:     {ev.timestamp}",
    ]
    return {"subject": subject, "text": "\n".join(lines)}


def render_signal(ev: Event) -> Dict[str, str]:
    sig_cn = {
        "stop_loss": "硬止损",
        "take_profit": "止盈触发",
        "max_holding_days": "持股到期",
        "condition_fail": "条件不符",
    }.get(ev.payload.get("signal", ""), ev.payload.get("signal", ""))
    subject = f"[{sig_cn}] {ev.payload.get('code')} {ev.payload.get('name', '')}"
    lines = [
        f"实例:    #{ev.payload.get('instance_id')}",
        f"标的:    {ev.payload.get('code')} {ev.payload.get('name', '')}",
        f"信号:    {sig_cn}",
        f"收益率:  {ev.payload.get('profit_pct', 0) * 100:.2f}%",
        f"时间:    {ev.timestamp}",
    ]
    return {"subject": subject, "text": "\n".join(lines)}


def render_selector(ev: Event) -> Dict[str, str]:
    subject = f"[选股完成] 命中 {ev.payload.get('hit_count', 0)} 条"
    lines = [
        f"记录 ID:  {ev.payload.get('record_id')}",
        f"自然语言: {ev.payload.get('natural_lang', '')}",
        f"命中数量: {ev.payload.get('hit_count', 0)}",
    ]
    return {"subject": subject, "text": "\n".join(lines)}


RENDERERS = {
    "backtest.completed": render_backtest_completed,
    "trade.executed": render_trade,
    "signal.triggered": render_signal,
    "selector.completed": render_selector,
}


# ============================================================
# 通知管理器
# ============================================================
class Notifier:
    """订阅事件 -> 渲染 -> 发送邮件。"""

    def __init__(
        self,
        smtp: Optional[SmtpClient],
        to_addrs: List[str],
        bus: Optional[EventBus] = None,
        event_types: Optional[List[str]] = None,
    ) -> None:
        self.smtp = smtp
        self.to_addrs = to_addrs
        self.bus = bus or get_bus()
        self.event_types = event_types or list(RENDERERS.keys())
        self._unsubscribers: list = []
        for et in self.event_types:
            fn = self._make_handler(et)
            self.bus.subscribe(et, fn)
            self._unsubscribers.append((et, fn))

    def _make_handler(self, event_type: str):
        def _handle(ev: Event) -> None:
            self._dispatch(event_type, ev)
        _handle.__name__ = f"notify_{event_type.replace('.', '_')}"
        return _handle

    def _dispatch(self, event_type: str, ev: Event) -> None:
        if self.smtp is None or not self.to_addrs:
            return
        render = RENDERERS.get(event_type)
        if render is None:
            return
        try:
            msg = render(ev)
            self.smtp.send(
                subject=msg["subject"],
                body_text=msg["text"],
                to_addrs=self.to_addrs,
            )
        except Exception as e:
            logger.warning("通知发送失败 [%s]: %s", event_type, e)

    def shutdown(self) -> None:
        for et, fn in self._unsubscribers:
            self.bus.unsubscribe(et, fn)
        self._unsubscribers.clear()


def build_default_notifier(config: Dict[str, Any]) -> Optional[Notifier]:
    """根据 settings.yaml 构造 Notifier（如未配置则返回 None）。"""
    notify_cfg = config.get("notify", {})
    if not notify_cfg.get("enabled", False):
        return None
    smtp_cfg = SmtpConfig(
        host=notify_cfg.get("smtp_host", ""),
        port=int(notify_cfg.get("smtp_port", 465)),
        username=notify_cfg.get("smtp_user", ""),
        password=notify_cfg.get("smtp_password", ""),
        use_ssl=bool(notify_cfg.get("smtp_ssl", True)),
        from_addr=notify_cfg.get("from_addr", ""),
    )
    smtp = SmtpClient(smtp_cfg) if smtp_cfg.host else None
    to_addrs = [
        a.strip() for a in notify_cfg.get("to_addrs", "").split(",") if a.strip()
    ]
    if not smtp or not to_addrs:
        logger.warning("通知配置不完整（缺 smtp_host 或 to_addrs）")
        return None
    return Notifier(smtp=smtp, to_addrs=to_addrs)
