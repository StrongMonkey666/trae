"""对比多个回测结果。

用法：python -m scripts.compare_backtests 1 2 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.backtest.records import BacktestRecordStore
from quant_platform.utils.config import load_config
from quant_platform.utils.logger import get_logger, setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="对比回测结果")
    parser.add_argument("ids", nargs="+", type=int, help="回测记录 ID 列表")
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(
        level=cfg.get("logging", {}).get("level", "INFO"),
        log_file=cfg.get("logging", {}).get("file"),
    )
    log = get_logger("scripts.compare_backtests")
    store = BacktestRecordStore(cfg["data_service"]["storage"]["sqlite_path"])

    records = store.compare(args.ids)
    log.info("对比 %d 条", len(records))

    cols = ["name", "trade_count", "total_return", "annualized_return",
            "win_rate", "max_drawdown", "sharpe_ratio", "final_equity"]
    header = f"{'ID':>4} " + " ".join(f"{c:>20}" for c in cols)
    print(header)
    print("-" * len(header))
    for r in records:
        m = r.metrics
        print(
            f"{r.id:>4} "
            + " ".join(
                f"{str(r.name)[:20]:>20}" if c == "name"
                else f"{m.get(c, 0):>20.4f}" if isinstance(m.get(c, 0), float)
                else f"{m.get(c, 0):>20}"
                for c in cols
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
