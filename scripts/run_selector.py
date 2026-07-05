"""选股 CLI 入口。

用法：
    # 使用预置模板
    python -m scripts.run_selector --template low_valuation

    # 使用自然语言
    python -m scripts.run_selector --nl "PE 小于 20 且 ROE 大于 10% 的股票"

    # 使用结构化 JSON
    python -m scripts.run_selector --json '{
        "conditions":[{"field":"pe_ttm","operator":"<","value":20}],
        "limit":10
    }'
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.llm.openai_compatible import OpenAICompatibleClient
from quant_platform.selector.service import SelectorService
from quant_platform.utils.config import load_config
from quant_platform.utils.logger import get_logger, setup_logging
from quant_platform.selector.schema import SelectorSpec


def main() -> int:
    parser = argparse.ArgumentParser(description="自然语言/结构化选股")
    parser.add_argument("--nl", help="自然语言选股描述")
    parser.add_argument("--template", help="预置模板 key")
    parser.add_argument("--json", help="结构化条件 JSON")
    parser.add_argument("--list-templates", action="store_true", help="列出所有预置模板")
    parser.add_argument("--no-save", action="store_true", help="不保存选股记录")
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(
        level=cfg.get("logging", {}).get("level", "INFO"),
        log_file=cfg.get("logging", {}).get("file"),
    )
    log = get_logger("scripts.run_selector")

    if args.list_templates:
        for t in SelectorService.list_builtin_templates():
            print(f"[{t['key']}] {t['name']} - {t['description']}")
        return 0

    llm = None
    if args.nl:
        llm_cfg = cfg.get("llm", {})
        llm = OpenAICompatibleClient(
            api_key=llm_cfg.get("api_key", ""),
            base_url=llm_cfg.get("base_url", "https://api.openai.com/v1"),
            model=llm_cfg.get("model", "gpt-4o-mini"),
        )

    svc = SelectorService(llm_client=llm)

    if args.template:
        out = svc.run_template(args.template, save=not args.no_save)
    elif args.nl:
        out = svc.from_natural_language(args.nl, save=not args.no_save)
    elif args.json:
        spec = SelectorSpec.from_dict(json.loads(args.json))
        out = svc.run(spec, save=not args.no_save)
    else:
        parser.error("需要 --nl / --template / --json 之一")

    spec = out["spec"]
    result = out["result"]
    log.info("条件: %s", spec.to_dict())
    log.info("命中 %d 条", len(result))
    if not result.empty:
        cols = [c for c in ("code", "name", "close", "pe_ttm", "pb", "change_pct") if c in result.columns]
        print(result[cols].head(20).to_string(index=False))

    # 发布事件
    try:
        from quant_platform.eventbus.bus import get_bus
        from quant_platform.eventbus.events import SelectorCompletedEvent
        get_bus().publish(SelectorCompletedEvent(
            event_type="selector.completed",
            source="selector",
            record_id=out.get("record_id", -1),
            hit_count=len(result),
            natural_lang=args.nl or "",
            payload={
                "record_id": out.get("record_id", -1),
                "hit_count": len(result),
                "natural_lang": args.nl or "",
            },
        ))
    except Exception as e:
        log.debug("事件发布失败: %s", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
