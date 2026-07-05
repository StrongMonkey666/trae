"""手动触发一次数据更新。

用法：
    python -m scripts.update_data --task stock_list
    python -m scripts.update_data --task history --code 600519
    python -m scripts.update_data --task realtime --code 600519,000001
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.utils.config import load_config
from quant_platform.utils.logger import get_logger, setup_logging
from quant_platform.data_service.unified_api import UnifiedDataService


def main() -> int:
    parser = argparse.ArgumentParser(description="手动触发数据更新")
    parser.add_argument(
        "--task", required=True,
        choices=["stock_list", "history", "realtime", "financial"],
    )
    parser.add_argument("--code", help="股票代码，多个用逗号分隔")
    parser.add_argument("--start", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--freq", default="D", help="K线频率: D/W/M/1m/5m...")
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(
        level=cfg.get("logging", {}).get("level", "INFO"),
        log_file=cfg.get("logging", {}).get("file"),
    )
    log = get_logger("scripts.update_data")
    service = UnifiedDataService(config=cfg)

    if args.task == "stock_list":
        n = service.sync_stock_list()
        log.info("已同步股票列表 %d 条", n)

    elif args.task == "history":
        if not args.code:
            log.error("history 任务需要 --code")
            return 2
        end = date.fromisoformat(args.end) if args.end else date.today()
        start = (
            date.fromisoformat(args.start)
            if args.start
            else end - timedelta(days=365 * 5)
        )
        for c in args.code.split(","):
            n = service.sync_history(c.strip(), start=start, end=end, freq=args.freq)
            log.info("%s 同步 %d 条", c, n)

    elif args.task == "realtime":
        codes = args.code.split(",") if args.code else None
        df = service.get_realtime_data(codes=codes)
        log.info("实时行情拉取 %d 条", len(df))
        if not df.empty:
            print(df.head(20).to_string(index=False))

    elif args.task == "financial":
        if not args.code:
            log.error("financial 任务需要 --code")
            return 2
        end = date.fromisoformat(args.end) if args.end else date.today()
        start = (
            date.fromisoformat(args.start)
            if args.start
            else end - timedelta(days=365 * 5)
        )
        for c in args.code.split(","):
            n = service.sync_financial(c.strip(), start=start, end=end)
            log.info("%s 同步 %d 条", c, n)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
