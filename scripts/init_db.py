"""初始化数据库与数据目录。

执行：python -m scripts.init_db
"""
from __future__ import annotations

import sys
from pathlib import Path

# 把 src 加入 path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.utils.config import load_config
from quant_platform.utils.logger import get_logger, setup_logging
from quant_platform.data_service.storage import DataStore


def main() -> int:
    cfg = load_config()
    setup_logging(
        level=cfg.get("logging", {}).get("level", "INFO"),
        log_file=cfg.get("logging", {}).get("file"),
    )
    log = get_logger("scripts.init_db")

    sqlite_path = cfg["data_service"]["storage"]["sqlite_path"]
    hdf5_path = cfg["data_service"]["storage"]["hdf5_path"]

    store = DataStore(sqlite_path=sqlite_path, hdf5_path=hdf5_path)
    log.info("初始化完成: sqlite=%s, hdf5=%s", sqlite_path, hdf5_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
