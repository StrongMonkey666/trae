"""回测管理路由。"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from flask import Blueprint, current_app, render_template, abort, jsonify, request

from ...backtest.strategy import StrategyConfig

bp = Blueprint("backtests", __name__, url_prefix="/backtests")


@bp.route("/")
def list_view():
    store = current_app.config["record_store"]
    rows = store.list_recent(limit=100)
    for r in rows:
        m = r.get("metrics", {})
        r["total_return_pct"] = m.get("total_return", 0) * 100
        r["max_dd_pct"] = m.get("max_drawdown", 0) * 100
        r["sharpe"] = m.get("sharpe_ratio", 0)
    return render_template("backtests/list.html", rows=rows)


@bp.route("/new")
def new_view():
    """回测创建页面。"""
    from ...selector.templates import list_templates
    return render_template(
        "backtests/new.html",
        templates=list_templates(),
        today=date.today().isoformat(),
    )


@bp.route("/api/create", methods=["POST"])
def api_create():
    """在 Web 上创建并执行回测。"""
    data = request.get_json(force=True, silent=True) or {}

    # 1. 构建 SelectorSpec
    template = data.get("template")
    json_spec = data.get("json_spec")
    if template:
        from ...selector.templates import get_template
        spec = get_template(template)
    elif json_spec:
        from ...selector.schema import SelectorSpec
        spec = SelectorSpec.from_dict(json_spec)
    else:
        return jsonify({"error": "需要选择模板或输入 JSON 条件"}), 400

    # 2. 构建 StrategyConfig
    try:
        start_date = date.fromisoformat(data.get("start_date", ""))
        end_date = date.fromisoformat(data.get("end_date", ""))
    except (ValueError, TypeError):
        return jsonify({"error": "日期格式无效，需 YYYY-MM-DD"}), 400

    cfg_dict: Dict[str, Any] = {
        "name": data.get("name", "web_backtest"),
        "start_date": str(start_date),
        "end_date": str(end_date),
        "initial_capital": float(data.get("initial_capital", 1_000_000)),
        "rebalance_freq": data.get("rebalance_freq", "weekly"),
        "selector": spec.to_dict(),
        "max_holdings": int(data.get("max_holdings", 5)),
        "max_buy_per_day": int(data.get("max_buy_per_day", 2)),
        "max_sell_per_day": int(data.get("max_sell_per_day", 3)),
        "take_profit_threshold": float(data.get("take_profit_threshold", 0.20)),
        "take_profit_drawdown": float(data.get("take_profit_drawdown", 0.05)),
        "stop_loss": float(data.get("stop_loss", -0.08)),
        "max_holding_days": int(data.get("max_holding_days", 30)),
        "fee_rate": float(data.get("fee_rate", 0.0003)),
        "stamp_tax": float(data.get("stamp_tax", 0.001)),
        "slippage": float(data.get("slippage", 0.001)),
    }
    capital_model = data.get("capital_model", "equal_weight")
    if capital_model:
        cfg_dict["capital_model"] = capital_model
    if capital_model == "fixed_amount":
        cfg_dict["fixed_amount"] = float(data.get("fixed_amount", 10_000))

    try:
        config = StrategyConfig.from_dict(cfg_dict)
        config.validate()
    except Exception as e:
        return jsonify({"error": f"参数校验失败: {e}"}), 400

    # 3. 执行回测
    try:
        from ...backtest.engine import BacktestEngine
        data_svc = current_app.config["data_service"]
        engine = BacktestEngine(data_service=data_svc)

        universe_str = data.get("universe", "")
        universe = [c.strip() for c in universe_str.split(",") if c.strip()] or None

        result = engine.run(config, universe=universe)
    except Exception as e:
        return jsonify({"error": f"回测执行失败: {e}"}), 500

    # 4. 序列化结果
    trades_out = [t.to_dict() for t in result.trades]
    equity_out = [
        {"date": str(d.date()), "value": float(v)}
        for d, v in zip(result.equity_curve["date"], result.equity_curve["value"])
    ]
    metrics_out = result.metrics.to_dict()

    # 5. 持久化
    try:
        store = current_app.config["record_store"]
        record_id = store.save(
            name=config.name,
            config=config,
            metrics=metrics_out,
            trade_count=len(result.trades),
            trades=trades_out,
            equity_curve=equity_out,
        )
    except Exception as e:
        return jsonify({"error": f"回测成功但保存失败: {e}"}), 500

    return jsonify({
        "ok": True,
        "record_id": record_id,
        "metrics": metrics_out,
        "trade_count": len(result.trades),
    })


@bp.route("/<int:record_id>")
def detail(record_id: int):
    store = current_app.config["record_store"]
    rec = store.get(record_id)
    if rec is None:
        abort(404)
    return render_template(
        "backtests/detail.html",
        rec=rec,
        metrics=rec.metrics,
        equity_curve=rec.equity_curve or [],
        trades=rec.trades or [],
        config=rec.config,
    )


@bp.route("/<int:record_id>/equity.json")
def equity_json(record_id: int):
    """返回 equity curve 数据（给 Chart.js 用）。"""
    store = current_app.config["record_store"]
    rec = store.get(record_id)
    if rec is None:
        return jsonify({"error": "not found"}), 404
    dates = [p["date"] for p in (rec.equity_curve or [])]
    values = [p["value"] for p in (rec.equity_curve or [])]
    return jsonify({"dates": dates, "values": values})


@bp.route("/<int:record_id>/trades.json")
def trades_json(record_id: int):
    store = current_app.config["record_store"]
    rec = store.get(record_id)
    if rec is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"trades": rec.trades or []})
