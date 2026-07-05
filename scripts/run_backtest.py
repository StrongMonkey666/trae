"""回测 CLI 入口。

用法：
    # 加载预置模板并回测
    python -m scripts.run_backtest --template low_valuation --start 2024-01-01 --end 2024-12-31

    # 用结构化策略 JSON 回测
    python -m scripts.run_backtest --config strategy.json

    # 用自然语言生成条件 + 回测
    python -m scripts.run_backtest --nl "PE 小于 20 且 ROE 大于 10% 的股票" --start 2024-01-01 --end 2024-12-31
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.backtest.engine import BacktestEngine
from quant_platform.backtest.strategy import StrategyConfig
from quant_platform.llm.openai_compatible import OpenAICompatibleClient
from quant_platform.selector.schema import SelectorSpec
from quant_platform.selector.templates import get_template
from quant_platform.utils.config import load_config
from quant_platform.utils.logger import get_logger, setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="回测执行入口")
    parser.add_argument("--config", help="策略 JSON 配置文件")
    parser.add_argument("--template", help="预置选股模板 key")
    parser.add_argument("--nl", help="自然语言选股描述（需要 LLM）")
    parser.add_argument("--start", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=1_000_000)
    parser.add_argument("--freq", default="weekly", choices=["daily", "weekly", "monthly"])
    parser.add_argument("--universe", help="股票代码列表（逗号分隔），默认全市场")
    parser.add_argument("--output", help="结果输出 JSON 路径")
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(
        level=cfg.get("logging", {}).get("level", "INFO"),
        log_file=cfg.get("logging", {}).get("file"),
    )
    log = get_logger("scripts.run_backtest")

    # 1. 加载 selector spec
    if args.config:
        config = StrategyConfig.from_json(Path(args.config).read_text(encoding="utf-8"))
    else:
        if args.template:
            spec = get_template(args.template)
        elif args.nl:
            llm_cfg = cfg.get("llm", {})
            llm = OpenAICompatibleClient(
                api_key=llm_cfg.get("api_key", ""),
                base_url=llm_cfg.get("base_url", "https://api.openai.com/v1"),
                model=llm_cfg.get("model", "gpt-4o-mini"),
            )
            from quant_platform.llm.parser import natural_language_to_spec
            spec = SelectorSpec.from_dict(natural_language_to_spec(llm, args.nl))
        else:
            parser.error("需要 --config / --template / --nl 之一")
        config = StrategyConfig(
            name=args.template or "nl_backtest",
            start_date=date.fromisoformat(args.start) if args.start else None,
            end_date=date.fromisoformat(args.end) if args.end else None,
            initial_capital=args.capital,
            rebalance_freq=args.freq,
            selector=spec,
        )

    # 2. 准备 universe
    universe = (
        [c.strip() for c in args.universe.split(",")] if args.universe else None
    )

    # 3. 执行
    engine = BacktestEngine()
    result = engine.run(config, universe=universe)
    log.info("回测完成: %s", result.metrics.to_dict())

    out = {
        "strategy": config.to_dict(),
        "metrics": result.metrics.to_dict(),
        "trade_count": len(result.trades),
        "trades": [t.to_dict() for t in result.trades],
        "equity_curve": [
            {"date": str(d.date()), "value": float(v)}
            for d, v in zip(result.equity_curve["date"], result.equity_curve["value"])
        ],
    }
    if args.output:
        Path(args.output).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("结果已写入 %s", args.output)
    else:
        print(json.dumps(out["metrics"], ensure_ascii=False, indent=2))
        print("---")
        print(f"交易笔数: {len(result.trades)}")
        for t in result.trades[:10]:
            print(t.to_dict())

    # 4. 写入回测记录 + 发布事件
    try:
        from quant_platform.backtest.records import BacktestRecordStore
        from quant_platform.eventbus.bus import get_bus
        from quant_platform.eventbus.events import BacktestCompletedEvent
        sqlite_path = cfg["data_service"]["storage"]["sqlite_path"]
        record_store = BacktestRecordStore(sqlite_path)
        record_id = record_store.save(
            name=config.name,
            config=config,
            metrics=result.metrics.to_dict(),
            trade_count=len(result.trades),
            trades=out["trades"],
            equity_curve=out["equity_curve"],
        )
        log.info("回测已存入记录 #%d", record_id)
        get_bus().publish(BacktestCompletedEvent(
            event_type="backtest.completed",
            source="backtest",
            record_id=record_id,
            name=config.name,
            metrics=result.metrics.to_dict(),
            payload={
                "record_id": record_id,
                "name": config.name,
                "metrics": result.metrics.to_dict(),
            },
        ))
    except Exception as e:
        log.warning("回测记录/事件发布失败: %s", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
