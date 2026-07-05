"""模拟持仓系统：状态持久化。

设计目标：
- 内存中维护组合状态
- 每次 tick 后持久化到 SQLite，支持崩溃恢复
- 状态表：sim_state（单一记录）/ sim_position（持仓）/ sim_trade（成交历史）
- 快照表：sim_snapshot（每日权益快照）
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger
from ..backtest.position import Position, Trade

logger = get_logger(__name__)


class SimState:
    """模拟器状态存储。"""

    def __init__(self, sqlite_path: str | Path) -> None:
        self.path = Path(sqlite_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(str(self.path), detect_types=sqlite3.PARSE_DECLTYPES)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _init_schema(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sim_instance (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    name            TEXT NOT NULL,
                    config_json     TEXT NOT NULL,
                    backtest_id     INTEGER,
                    status          TEXT DEFAULT 'running',  -- running/paused/stopped
                    initial_capital REAL NOT NULL,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS sim_position (
                    instance_id     INTEGER NOT NULL,
                    code            TEXT NOT NULL,
                    name            TEXT,
                    shares          INTEGER NOT NULL,
                    avg_cost        REAL NOT NULL,
                    buy_date        DATE,
                    peak_price      REAL DEFAULT 0,
                    in_tp_zone      INTEGER DEFAULT 0,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (instance_id, code)
                );

                CREATE TABLE IF NOT EXISTS sim_cash (
                    instance_id     INTEGER PRIMARY KEY,
                    cash            REAL NOT NULL,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS sim_trade (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    instance_id     INTEGER NOT NULL,
                    code            TEXT NOT NULL,
                    name            TEXT,
                    side            TEXT NOT NULL,     -- buy/sell
                    price           REAL NOT NULL,
                    shares          INTEGER NOT NULL,
                    amount          REAL NOT NULL,     -- 成交金额
                    fee             REAL,
                    tax             REAL,
                    reason          TEXT,
                    trade_date      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS sim_snapshot (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    instance_id     INTEGER NOT NULL,
                    snap_date       DATE NOT NULL,
                    cash            REAL NOT NULL,
                    position_value  REAL NOT NULL,
                    total_value     REAL NOT NULL,
                    pnl             REAL NOT NULL,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_sim_trade_instance
                    ON sim_trade(instance_id, trade_date DESC);
                CREATE INDEX IF NOT EXISTS idx_sim_snapshot_instance
                    ON sim_snapshot(instance_id, snap_date);
                """
            )

    # ============================================================
    # 实例管理
    # ============================================================
    def create_instance(
        self,
        name: str,
        config_json: str,
        initial_capital: float,
        backtest_id: Optional[int] = None,
    ) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO sim_instance
                    (name, config_json, backtest_id, initial_capital)
                VALUES (?, ?, ?, ?)
                """,
                (name, config_json, backtest_id, initial_capital),
            )
            inst_id = cur.lastrowid
            conn.execute(
                "INSERT INTO sim_cash (instance_id, cash) VALUES (?, ?)",
                (inst_id, initial_capital),
            )
            return inst_id

    def get_instance(self, instance_id: int) -> Optional[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sim_instance WHERE id=?", (instance_id,)
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def list_instances(self) -> List[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sim_instance ORDER BY id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_instance_status(self, instance_id: int, status: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE sim_instance
                SET status=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (status, instance_id),
            )

    # ============================================================
    # 现金 / 持仓
    # ============================================================
    def get_cash(self, instance_id: int) -> float:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT cash FROM sim_cash WHERE instance_id=?",
                (instance_id,),
            ).fetchone()
        return float(row["cash"]) if row else 0.0

    def set_cash(self, instance_id: int, cash: float) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO sim_cash (instance_id, cash) VALUES (?, ?)
                ON CONFLICT(instance_id) DO UPDATE SET
                    cash=excluded.cash, updated_at=CURRENT_TIMESTAMP
                """,
                (instance_id, cash),
            )

    def get_positions(self, instance_id: int) -> List[Position]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sim_position WHERE instance_id=?",
                (instance_id,),
            ).fetchall()
        out: List[Position] = []
        for r in rows:
            d = dict(r)
            out.append(Position(
                code=d["code"],
                name=d.get("name") or "",
                shares=d["shares"],
                avg_cost=d["avg_cost"],
                buy_date=d["buy_date"],
                peak_price=d.get("peak_price", 0.0),
                in_tp_zone=bool(d.get("in_tp_zone", 0)),
            ))
        return out

    def upsert_position(self, instance_id: int, pos: Position) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO sim_position
                    (instance_id, code, name, shares, avg_cost,
                     buy_date, peak_price, in_tp_zone)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instance_id, code) DO UPDATE SET
                    name=excluded.name,
                    shares=excluded.shares,
                    avg_cost=excluded.avg_cost,
                    buy_date=excluded.buy_date,
                    peak_price=excluded.peak_price,
                    in_tp_zone=excluded.in_tp_zone,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    instance_id, pos.code, pos.name, pos.shares,
                    pos.avg_cost, pos.buy_date,
                    pos.peak_price, 1 if pos.in_tp_zone else 0,
                ),
            )

    def delete_position(self, instance_id: int, code: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "DELETE FROM sim_position WHERE instance_id=? AND code=?",
                (instance_id, code),
            )

    # ============================================================
    # 成交记录
    # ============================================================
    def add_trade(
        self,
        instance_id: int,
        code: str,
        name: str,
        side: str,
        price: float,
        shares: int,
        amount: float,
        fee: float = 0.0,
        tax: float = 0.0,
        reason: str = "",
    ) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO sim_trade
                    (instance_id, code, name, side, price, shares,
                     amount, fee, tax, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance_id, code, name, side, price, shares,
                    amount, fee, tax, reason,
                ),
            )
            return cur.lastrowid

    def list_trades(
        self, instance_id: int, limit: int = 100
    ) -> List[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sim_trade WHERE instance_id=?
                ORDER BY id DESC LIMIT ?
                """,
                (instance_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ============================================================
    # 快照（每日权益）
    # ============================================================
    def save_snapshot(
        self,
        instance_id: int,
        snap_date: date,
        cash: float,
        position_value: float,
        total_value: float,
        pnl: float,
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO sim_snapshot
                    (instance_id, snap_date, cash, position_value,
                     total_value, pnl)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (instance_id, snap_date, cash, position_value,
                 total_value, pnl),
            )

    def list_snapshots(self, instance_id: int) -> List[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sim_snapshot WHERE instance_id=?
                ORDER BY snap_date ASC
                """,
                (instance_id,),
            ).fetchall()
        return [dict(r) for r in rows]
