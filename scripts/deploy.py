"""一键部署：把回测结果部署为模拟持仓实例。

用法：python -m scripts.deploy 1 [--poll 3] [--run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.backtest.records import BacktestRecordStore
from quant_platform.simulator.engine import SimulatedHoldingEngine
from quant_platform.simulator.state import SimState
from quant_platform.utils.config import load_config
from quant_platform.utils.logger import get_logger, setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="一键部署回测到模拟持仓")
    parser.add_argument("record_id", type=int, help="回测记录 ID")
    parser.add_argument(
        "--suffix", default="", help="实例名后缀",
    )
    parser.add_argument(
        "--run", action="store_true", help="部署后立即运行（阻塞）",
    )
    parser.add_argument(
        "--poll", type=float, default=3.0, help="轮询间隔（秒）",
    )
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(
        level=cfg.get("logging", {}).get("level", "INFO"),
        log_file=cfg.get("logging", {}).get("file"),
    )
    log = get_logger("scripts.deploy")
    sqlite_path = cfg["data_service"]["storage"]["sqlite_path"]
    record_store = BacktestRecordStore(sqlite_path)
    state = SimState(sqlite_path)

    engine = SimulatedHoldingEngine.deploy_from_record(
        record_id=args.record_id,
        record_store=record_store,
        state=state,
        name_suffix=args.suffix,
    )
    log.info("已部署为实例 #%d (回测 #%d)", engine.instance_id, args.record_id)
    print(f"已创建模拟实例 #{engine.instance_id}")
    print(f"  初始资金 = {state.get_cash(engine.instance_id):.2f}")
    if args.run:
        log.info("开始运行模拟...")
        engine.start(poll_seconds=args.poll)
    else:
        print("可运行 `python scripts/run_simulator.py " f"{engine.instance_id}` 启动")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
