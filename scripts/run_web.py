"""启动 Web 服务。

用法：python -m scripts.run_web [--host 0.0.0.0] [--port 5000] [--debug]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.utils.config import load_config
from quant_platform.utils.logger import get_logger, setup_logging
from quant_platform.web.app import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 Web 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(
        level=cfg.get("logging", {}).get("level", "INFO"),
        log_file=cfg.get("logging", {}).get("file"),
    )
    log = get_logger("scripts.run_web")

    app = create_app(config=cfg)
    log.info("Web 服务启动: http://%s:%d", args.host, args.port)
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
