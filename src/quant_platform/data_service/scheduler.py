"""调度器：APScheduler + 交易日历感知。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..utils.config import deep_get
from ..utils.logger import get_logger
from ..utils.trading_calendar import TradingCalendar
from .unified_api import UnifiedDataService

logger = get_logger(__name__)


def _is_trading_day() -> bool:
    return TradingCalendar().is_trading_day(date.today())


class DataScheduler:
    """根据配置自动启动定时同步任务。"""

    def __init__(
        self,
        service: UnifiedDataService,
        config: Dict[str, Any],
    ) -> None:
        self.service = service
        self.config = config
        self.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self._setup_jobs()

    def _setup_jobs(self) -> None:
        jobs_cfg = deep_get(self.config, "scheduler", "jobs", default={}) or {}
        for name, jcfg in jobs_cfg.items():
            if not jcfg.get("enabled", True):
                continue
            self._add_job(name, jcfg)

    def _add_job(self, name: str, jcfg: Dict[str, Any]) -> None:
        only_trading = jcfg.get("only_trading_day", False)
        trigger_cfg = jcfg.get("trigger", "interval")
        if trigger_cfg == "interval":
            seconds = int(jcfg.get("seconds", 60))
            trigger = IntervalTrigger(seconds=seconds)
        elif trigger_cfg == "cron":
            trigger = CronTrigger.from_crontab(self._build_cron(jcfg))
        else:
            logger.warning("未知 trigger 类型: %s, 跳过任务 %s", trigger_cfg, name)
            return

        job = self.scheduler.add_job(
            self._make_runner(name),
            trigger=trigger,
            id=name,
            replace_existing=True,
        )
        logger.info(
            "注册定时任务 [%s]: trigger=%s, only_trading=%s, next_run=%s",
            name, trigger_cfg, only_trading, job.next_run_time,
        )

    @staticmethod
    def _build_cron(jcfg: Dict[str, Any]) -> str:
        return (
            f"{jcfg.get('minute', '*')} "
            f"{jcfg.get('hour', '*')} "
            f"{jcfg.get('day', '*')} "
            f"{jcfg.get('month', '*')} "
            f"{jcfg.get('day_of_week', '*')}"
        )

    def _make_runner(self, name: str):
        """根据任务名生成执行函数。"""
        if name == "realtime_poll":
            def _run():
                if not _is_trading_day():
                    return
                try:
                    n = self.service.sync_realtime()
                    logger.debug("realtime_poll 拉取 %d 条", n)
                except Exception as e:
                    logger.warning("realtime_poll 失败: %s", e)
            return _run
        if name == "daily_full_check":
            def _run():
                if not _is_trading_day():
                    return
                try:
                    self.service.sync_stock_list()
                    logger.info("daily_full_check 完成")
                except Exception as e:
                    logger.warning("daily_full_check 失败: %s", e)
            return _run
        # 默认空操作
        return lambda: None

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("DataScheduler 已启动")

    def shutdown(self, wait: bool = True) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=wait)
            logger.info("DataScheduler 已停止")

    def list_jobs(self):
        return [
            {"id": j.id, "next_run": j.next_run_time, "trigger": str(j.trigger)}
            for j in self.scheduler.get_jobs()
        ]
