"""回测管理路由。"""
from flask import Blueprint, current_app, render_template, abort, jsonify

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
