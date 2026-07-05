"""运行后台调度器（实时轮询 + 每日全量校验）。"""
from __future__ import annotations

import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.utils.config import load_config
from quant_platform.utils.logger import get_logger, setup_logging
from quant_platform.data_service.scheduler import DataScheduler
from quant_platform.data_service.unified_api import UnifiedDataService


def main() -> int:
    cfg = load_config()
    setup_logging(
        level=cfg.get("logging", {}).get("level", "INFO"),
        log_file=cfg.get("logging", {}).get("file"),
    )
    log = get_logger("scripts.run_scheduler")
    service = UnifiedDataService(config=cfg)
    sched = DataScheduler(service=service, config=cfg)
    sched.start()

    stop_flag = {"stop": False}

    def _on_signal(signum, _frame):
        log.info("收到信号 %s, 准备退出", signum)
        stop_flag["stop"] = True

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        while not stop_flag["stop"]:
            time.sleep(1)
    finally:
        sched.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
