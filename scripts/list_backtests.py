"""列出回测记录。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.backtest.records import BacktestRecordStore
from quant_platform.utils.config import load_config
from quant_platform.utils.logger import get_logger, setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="列出回测记录")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--name", help="按名称过滤")
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(
        level=cfg.get("logging", {}).get("level", "INFO"),
        log_file=cfg.get("logging", {}).get("file"),
    )
    log = get_logger("scripts.list_backtests")
    store = BacktestRecordStore(cfg["data_service"]["storage"]["sqlite_path"])

    if args.name:
        records = store.list_by_name(args.name)
        log.info("名称 %s 命中 %d 条", args.name, len(records))
        for r in records:
            print(f"#{r.id} {r.name} | {r.created_at} | trades={r.trade_count} | "
                  f"final={r.metrics.get('final_equity', 0):.0f}")
    else:
        rows = store.list_recent(args.limit)
        log.info("最近 %d 条", len(rows))
        print(f"{'ID':>4} {'名称':<24} {'交易':>4} {'收益率':>9} {'最大回撤':>9} {'夏普':>7} {'部署':>5} {'创建时间':<20}")
        for r in rows:
            m = r.get("metrics", {})
            print(
                f"{r['id']:>4} {r['name'][:24]:<24} {r['trade_count']:>4} "
                f"{m.get('total_return', 0)*100:>8.2f}% "
                f"{m.get('max_drawdown', 0)*100:>8.2f}% "
                f"{m.get('sharpe_ratio', 0):>7.2f} "
                f"{'是' if r['deployed'] else '否':>5} "
                f"{r['created_at']:<20}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
