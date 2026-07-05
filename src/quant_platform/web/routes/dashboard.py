"""仪表盘路由。"""
from flask import Blueprint, current_app, render_template

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def index():
    cfg = current_app.config["QUANT_CONFIG"]
    record_store = current_app.config["record_store"]
    sim_state = current_app.config["sim_state"]

    # 顶部统计
    backtests = record_store.list_recent(limit=200)
    instances = sim_state.list_instances()

    # 取最近 5 条回测的指标
    recent = []
    for r in backtests[:5]:
        m = r.get("metrics", {})
        recent.append({
            "id": r["id"],
            "name": r["name"],
            "created_at": r.get("created_at"),
            "total_return": m.get("total_return", 0) * 100,
            "sharpe": m.get("sharpe_ratio", 0),
            "max_dd": m.get("max_drawdown", 0) * 100,
            "deployed": r.get("deployed", False),
        })

    # 模拟实例概要
    sim_summary = []
    for inst in instances[:5]:
        cash = sim_state.get_cash(inst["id"])
        positions = sim_state.get_positions(inst["id"])
        snaps = sim_state.list_snapshots(inst["id"])
        latest_snap = snaps[-1] if snaps else None
        sim_summary.append({
            "id": inst["id"],
            "name": inst["name"],
            "status": inst.get("status", "unknown"),
            "cash": cash,
            "position_count": len(positions),
            "total_value": latest_snap["total_value"] if latest_snap else cash,
            "pnl": latest_snap["pnl"] if latest_snap else 0,
        })

    return render_template(
        "dashboard.html",
        cfg={"project_name": cfg.get("project", {}).get("name", "quant")},
        stats={
            "backtest_count": len(backtests),
            "instance_count": len(instances),
            "running_count": sum(
                1 for i in instances if i.get("status") == "running"
            ),
        },
        recent=recent,
        sim_summary=sim_summary,
    )
