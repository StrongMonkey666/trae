"""运行已部署的模拟持仓实例。

用法：python -m scripts.run_simulator <instance_id> [--poll 3]
"""
from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.backtest.records import BacktestRecordStore
from quant_platform.backtest.strategy import StrategyConfig
from quant_platform.simulator.engine import SimulatedHoldingEngine
from quant_platform.simulator.state import SimState
from quant_platform.utils.config import load_config
from quant_platform.utils.logger import get_logger, setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="运行模拟持仓")
    parser.add_argument("instance_id", type=int)
    parser.add_argument("--poll", type=float, default=3.0, help="轮询间隔（秒）")
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(
        level=cfg.get("logging", {}).get("level", "INFO"),
        log_file=cfg.get("logging", {}).get("file"),
    )
    log = get_logger("scripts.run_simulator")
    sqlite_path = cfg["data_service"]["storage"]["sqlite_path"]

    state = SimState(sqlite_path)
    inst = state.get_instance(args.instance_id)
    if inst is None:
        log.error("实例 #%d 不存在", args.instance_id)
        return 1
    log.info("加载实例 #%d: %s", inst["id"], inst["name"])

    config = StrategyConfig.from_json(inst["config_json"])
    engine = SimulatedHoldingEngine(
        instance_id=inst["id"],
        config=config,
        state=state,
    )

    def _on_signal(sig, _frm):
        log.info("收到信号 %s, 准备退出", sig)
        engine.stop()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    engine.start(poll_seconds=args.poll)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
