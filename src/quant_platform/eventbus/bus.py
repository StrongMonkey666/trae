"""事件总线核心：进程内 Pub/Sub。

设计目标：
- 解耦：发布者只关心 event_type，不关心谁订阅
- 简单：同步派发 + 异常隔离（一个订阅者抛错不影响其他人）
- 可观察：记录派发历史
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from ..utils.logger import get_logger
from .events import Event

logger = get_logger(__name__)


HandlerFn = Callable[[Event], None]


class EventBus:
    """进程内事件总线（单例）。"""

    _instance: Optional["EventBus"] = None
    _lock_cls = threading.Lock()

    def __new__(cls, *args, **kwargs):
        # 单例
        if cls._instance is None:
            with cls._lock_cls:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True
        self._lock = threading.RLock()
        self._handlers: Dict[str, List[HandlerFn]] = defaultdict(list)
        self._history: List[Event] = []
        self._max_history = 1000

    # ============================================================
    # 订阅
    # ============================================================
    def subscribe(self, event_type: str, handler: HandlerFn) -> None:
        """订阅事件类型。handler 签名: handler(event: Event) -> None"""
        with self._lock:
            self._handlers[event_type].append(handler)
        logger.debug("订阅 %s -> %s", event_type, getattr(handler, "__name__", handler))

    def subscribe_pattern(self, pattern: str, handler: HandlerFn) -> None:
        """订阅事件类型模式（支持 `*` 通配符）。"""
        # 把模式存为特殊键 "__pattern__:<pattern>"
        key = f"__pattern__:{pattern}"
        with self._lock:
            self._handlers[key].append(handler)

    def unsubscribe(self, event_type: str, handler: HandlerFn) -> None:
        with self._lock:
            if handler in self._handlers.get(event_type, []):
                self._handlers[event_type].remove(handler)

    # ============================================================
    # 发布
    # ============================================================
    def publish(self, event: Event) -> int:
        """发布事件，返回实际派发的订阅者数。"""
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
            # 收集所有匹配的 handler（精确 + 模式）
            handlers = list(self._handlers.get(event.event_type, []))
            for pat_key, fns in self._handlers.items():
                if not pat_key.startswith("__pattern__:"):
                    continue
                pat = pat_key.split(":", 1)[1]
                if self._match(pat, event.event_type):
                    handlers.extend(fns)
        # 派发（不持锁）
        for h in handlers:
            try:
                h(event)
            except Exception as e:
                logger.warning("事件处理器 %s 抛错: %s", h, e)
        return len(handlers)

    def publish_dict(self, event_type: str, payload: Dict[str, Any], source: str = "", category: str = "") -> int:
        """直接用 dict 发布（便捷方法）。"""
        from .events import Event
        ev = Event(
            event_type=event_type,
            category=category or event_type.split(".")[0] if "." in event_type else "system",
            source=source,
            payload=payload,
        )
        return self.publish(ev)

    @staticmethod
    def _match(pattern: str, event_type: str) -> bool:
        if pattern == "*":
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return event_type.startswith(prefix + ".") or event_type == prefix
        if pattern.startswith("*."):
            suffix = pattern[2:]
            return event_type.endswith("." + suffix)
        return pattern == event_type

    # ============================================================
    # 查询
    # ============================================================
    def history(self, event_type: Optional[str] = None, limit: int = 100) -> List[Event]:
        with self._lock:
            h = self._history
            if event_type:
                h = [e for e in h if e.event_type == event_type]
            return list(h[-limit:])

    def handler_count(self, event_type: str) -> int:
        with self._lock:
            return len(self._handlers.get(event_type, []))

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()


# 便捷全局访问
def get_bus() -> EventBus:
    return EventBus()
