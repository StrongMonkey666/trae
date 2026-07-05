"""事件处理器装饰器与基类。"""
from __future__ import annotations

import functools
from typing import Callable

from ..utils.logger import get_logger
from .bus import EventBus, get_bus
from .events import Event

logger = get_logger(__name__)


def handler(event_type: str, bus: EventBus | None = None) -> Callable:
    """装饰器：把函数注册为某个事件类型的处理器。"""
    def decorator(fn: Callable) -> Callable:
        (bus or get_bus()).subscribe(event_type, fn)
        fn.__event_type__ = event_type  # type: ignore
        logger.debug("注册事件处理器: %s -> %s", event_type, fn.__name__)
        return fn
    return decorator
