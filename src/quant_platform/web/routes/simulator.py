"""模拟持仓路由。"""
from flask import Blueprint, current_app, render_template, abort, jsonify, request

from ...simulator.engine import SimulatedHoldingEngine

bp = Blueprint("simulator", __name__, url_prefix="/simulator")


@bp.route("/api/deploy/<int:record_id>", methods=["POST"])
def api_deploy(record_id: int):
    """从回测记录部署为模拟实例。"""
    record_store = current_app.config["record_store"]
    sim_state = current_app.config["sim_state"]
    try:
        engine = SimulatedHoldingEngine.deploy_from_record(
            record_id=record_id,
            record_store=record_store,
            state=sim_state,
        )
        return jsonify({
            "success": True,
            "instance_id": engine.instance_id,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@bp.route("/")
def list_view():
    state = current_app.config["sim_state"]
    instances = state.list_instances()
    for inst in instances:
        cash = state.get_cash(inst["id"])
        positions = state.get_positions(inst["id"])
        snaps = state.list_snapshots(inst["id"])
        latest = snaps[-1] if snaps else None
        inst["cash"] = cash
        inst["position_count"] = len(positions)
        inst["total_value"] = latest["total_value"] if latest else cash
        inst["pnl"] = latest["pnl"] if latest else 0
        inst["pnl_pct"] = (
            (latest["pnl"] / inst["initial_capital"]) * 100
            if latest and inst["initial_capital"] > 0 else 0
        )
    return render_template("simulator/list.html", instances=instances)


@bp.route("/<int:instance_id>")
def detail(instance_id: int):
    state = current_app.config["sim_state"]
    inst = state.get_instance(instance_id)
    if inst is None:
        abort(404)
    cash = state.get_cash(instance_id)
    positions = state.get_positions(instance_id)
    trades = state.list_trades(instance_id, limit=100)
    snapshots = state.list_snapshots(instance_id)
    return render_template(
        "simulator/detail.html",
        inst=inst,
        cash=cash,
        positions=positions,
        trades=trades,
        snapshots=snapshots,
    )


@bp.route("/<int:instance_id>/equity.json")
def equity_json(instance_id: int):
    state = current_app.config["sim_state"]
    snaps = state.list_snapshots(instance_id)
    return jsonify({
        "dates": [str(s["snap_date"]) for s in snaps],
        "values": [s["total_value"] for s in snaps],
        "cash": [s["cash"] for s in snaps],
        "position_value": [s["position_value"] for s in snaps],
    })
