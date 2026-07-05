"""仪表盘路由。"""
from __future__ import annotations

import pandas as pd
from flask import Blueprint, current_app, render_template

bp = Blueprint("dashboard", __name__)


def _fetch_market_snapshot(data_service, top_n: int = 20):
    """拉取全市场实时行情，返回涨幅榜和市场概览统计。"""
    try:
        df = data_service.get_realtime_data()
    except Exception:
        df = pd.DataFrame()

    if df is None or df.empty:
        return [], {"up_count": 0, "down_count": 0, "flat_count": 0, "avg_change": 0}

    # 计算涨跌幅（percent）
    df["last"] = pd.to_numeric(df.get("last", 0), errors="coerce")
    df["pre_close"] = pd.to_numeric(df.get("pre_close", 0), errors="coerce")
    df["change_pct"] = ((df["last"] - df["pre_close"]) / df["pre_close"] * 100).fillna(0)

    # 格式化其他列（缺失列补 0）
    for col in ("turnover_rate", "volume", "amount", "market_cap", "pe_ttm", "pb"):
        s = df.get(col)
        if s is None:
            s = pd.Series([0.0] * len(df), index=df.index)
        df[col] = pd.to_numeric(s, errors="coerce").fillna(0)

    # 涨跌家数统计
    up_count = int((df["change_pct"] > 0).sum())
    down_count = int((df["change_pct"] < 0).sum())
    flat_count = len(df) - up_count - down_count
    avg_change = float(df["change_pct"].mean()) if len(df) else 0.0

    # 涨幅榜 Top N（排除 ST/退市等 pre_close <= 0 的异常值）
    valid = df[df["pre_close"] > 0].copy()
    top = valid.nlargest(top_n, "change_pct")[
        ["code", "name", "last", "change_pct", "turnover_rate", "amount", "market_cap", "pe_ttm", "pb"]
    ]

    top_rows = []
    for _, row in top.iterrows():
        top_rows.append({
            "code": str(row["code"]).zfill(6),
            "name": str(row.get("name", "")),
            "last": float(row["last"]),
            "change_pct": float(row["change_pct"]),
            "turnover_rate": float(row["turnover_rate"]),
            "amount": float(row["amount"]),
            "market_cap": float(row["market_cap"]),
            "pe_ttm": float(row["pe_ttm"]),
            "pb": float(row["pb"]),
        })

    return top_rows, {
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "avg_change": avg_change,
        "total_stocks": len(df),
    }


@bp.route("/")
def index():
    cfg = current_app.config["QUANT_CONFIG"]
    record_store = current_app.config["record_store"]
    sim_state = current_app.config["sim_state"]
    data_service = current_app.config["data_service"]

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

    # 市场实时行情：涨幅榜 + 涨跌统计
    top_gainers, market_stats = _fetch_market_snapshot(data_service, top_n=20)

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
        top_gainers=top_gainers,
        market_stats=market_stats,
    )
