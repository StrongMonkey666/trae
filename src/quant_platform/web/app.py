"""Web 界面（Flask）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from flask import (
    Flask, Blueprint, render_template, request, jsonify, redirect, url_for,
)

from ..backtest.records import BacktestRecordStore
from ..backtest.strategy import StrategyConfig
from ..data_service.unified_api import UnifiedDataService
from ..selector.service import SelectorService
from ..selector.templates import list_templates, get_template
from ..simulator.state import SimState
from ..utils.config import load_config
from ..utils.logger import get_logger, setup_logging


def create_app(config: Dict[str, Any] | None = None) -> Flask:
    cfg = config or load_config()
    setup_logging(
        level=cfg.get("logging", {}).get("level", "INFO"),
        log_file=cfg.get("logging", {}).get("file"),
    )
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.config["QUANT_CONFIG"] = cfg
    # 共享服务对象
    sqlite_path = cfg["data_service"]["storage"]["sqlite_path"]
    app.config["record_store"] = BacktestRecordStore(sqlite_path)
    app.config["sim_state"] = SimState(sqlite_path)
    app.config["data_service"] = UnifiedDataService(config=cfg)
    app.config["selector_service"] = SelectorService(
        data_service=app.config["data_service"],
        history=None,
    )

    # 全局模板变量
    @app.context_processor
    def inject_globals():
        return {
            "cfg": {
                "project_name": cfg.get("project", {}).get("name", "quant"),
            }
        }

    # 注册路由
    from .routes import dashboard, backtests, simulator, selector, settings
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(backtests.bp)
    app.register_blueprint(simulator.bp)
    app.register_blueprint(selector.bp)
    app.register_blueprint(settings.bp)
    return app
