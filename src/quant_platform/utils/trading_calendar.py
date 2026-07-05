"""A 股交易日历：识别工作日/节假日，避开非交易日。

当前为最小可用实现：周末视为非交易日，节假日通过外部 JSON 文件维护。
实际运行中可将节假日列表接入第三方 API 自动更新。
"""
from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterable, Optional

from .exceptions import QuantPlatformError
from .logger import get_logger

logger = get_logger(__name__)


# 默认 A 股交易时段（可按需扩展盘后/夜盘）
MORNING_OPEN = time(9, 30)
MORNING_CLOSE = time(11, 30)
AFTERNOON_OPEN = time(13, 0)
AFTERNOON_CLOSE = time(15, 0)


class TradingCalendar:
    def __init__(self, holidays: Optional[Iterable[str]] = None) -> None:
        # 内部保存为 date 集合
        self._holidays: set[date] = set()
        if holidays:
            for h in holidays:
                self._holidays.add(self._parse(h))

    @staticmethod
    def _parse(d: str | date) -> date:
        if isinstance(d, date):
            return d
        return datetime.strptime(d, "%Y-%m-%d").date()

    @classmethod
    def from_file(cls, path: str | Path) -> "TradingCalendar":
        p = Path(path)
        if not p.is_file():
            logger.warning("交易日历文件不存在: %s，将视为无节假日", p)
            return cls()
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise QuantPlatformError("交易日历文件必须是字符串列表")
        return cls(holidays=data)

    def is_holiday(self, d: date) -> bool:
        return d in self._holidays

    def is_weekend(self, d: date) -> bool:
        return d.weekday() >= 5  # 周六=5, 周日=6

    def is_trading_day(self, d: date) -> bool:
        return not (self.is_weekend(d) or self.is_holiday(d))

    def is_trading_time(self, dt: datetime) -> bool:
        """简单判断当前是否在 A 股交易时段（不含节假日判断）。"""
        d = dt.date()
        if not self.is_trading_day(d):
            return False
        t = dt.time()
        return (MORNING_OPEN <= t <= MORNING_CLOSE) or (
            AFTERNOON_OPEN <= t <= AFTERNOON_CLOSE
        )
