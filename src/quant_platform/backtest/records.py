"""回测记录系统。

存储 / 查询 / 对比 / 部署状态。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger
from .strategy import StrategyConfig

logger = get_logger(__name__)


class BacktestRecord:
    """单条回测记录（写入/读取用）。"""

    def __init__(
        self,
        name: str,
        config: StrategyConfig,
        metrics: Dict[str, Any],
        trade_count: int,
        trades: List[Dict[str, Any]] = None,
        equity_curve: List[Dict[str, Any]] = None,
        id: Optional[int] = None,
        created_at: Optional[str] = None,
        deployed: bool = False,
        deployed_at: Optional[str] = None,
        notes: str = "",
    ) -> None:
        self.id = id
        self.name = name
        self.config = config
        self.metrics = metrics
        self.trade_count = trade_count
        self.trades = trades or []
        self.equity_curve = equity_curve or []
        self.created_at = created_at or datetime.now().isoformat(timespec="seconds")
        self.deployed = deployed
        self.deployed_at = deployed_at
        self.notes = notes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "config": self.config.to_dict(),
            "metrics": self.metrics,
            "trade_count": self.trade_count,
            "created_at": self.created_at,
            "deployed": self.deployed,
            "deployed_at": self.deployed_at,
            "notes": self.notes,
        }


class BacktestRecordStore:
    """SQLite 存储回测记录。"""

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
                CREATE TABLE IF NOT EXISTS backtest_record (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    name            TEXT NOT NULL,
                    config_json     TEXT NOT NULL,
                    metrics_json    TEXT NOT NULL,
                    trade_count     INTEGER NOT NULL,
                    trades_json     TEXT,
                    equity_curve    TEXT,
                    deployed        INTEGER DEFAULT 0,
                    deployed_at     TIMESTAMP,
                    notes           TEXT,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_record_name ON backtest_record(name);
                CREATE INDEX IF NOT EXISTS idx_record_created ON backtest_record(created_at DESC);
                """
            )

    # ============================================================
    # 写入
    # ============================================================
    def save(
        self,
        name: str,
        config: StrategyConfig,
        metrics: Dict[str, Any],
        trade_count: int,
        trades: Optional[List[Dict[str, Any]]] = None,
        equity_curve: Optional[List[Dict[str, Any]]] = None,
        notes: str = "",
    ) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO backtest_record
                    (name, config_json, metrics_json, trade_count,
                     trades_json, equity_curve, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    config.to_json(),
                    json.dumps(metrics, ensure_ascii=False),
                    trade_count,
                    json.dumps(trades or [], ensure_ascii=False, default=str),
                    json.dumps(equity_curve or [], ensure_ascii=False, default=str),
                    notes,
                ),
            )
            return cur.lastrowid

    def mark_deployed(self, record_id: int) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE backtest_record
                SET deployed=1, deployed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (record_id,),
            )

    def update_notes(self, record_id: int, notes: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE backtest_record SET notes=? WHERE id=?",
                (notes, record_id),
            )

    # ============================================================
    # 读取
    # ============================================================
    def get(self, record_id: int) -> Optional[BacktestRecord]:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM backtest_record WHERE id=?", (record_id,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_record(dict(row))

    def list_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """列表（精简版，不含完整 trades/equity_curve）。"""
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, name, metrics_json, trade_count,
                       deployed, deployed_at, notes, created_at
                FROM backtest_record
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["metrics"] = json.loads(d.pop("metrics_json"))
            out.append(d)
        return out

    def list_by_name(self, name: str) -> List[BacktestRecord]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM backtest_record WHERE name=? ORDER BY id DESC",
                (name,),
            ).fetchall()
        return [self._row_to_record(dict(r)) for r in rows]

    # ============================================================
    # 对比
    # ============================================================
    def compare(self, record_ids: List[int]) -> List[BacktestRecord]:
        if not record_ids:
            return []
        placeholders = ",".join("?" * len(record_ids))
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM backtest_record WHERE id IN ({placeholders})",
                record_ids,
            ).fetchall()
        return [self._row_to_record(dict(r)) for r in rows]

    # ============================================================
    # helpers
    # ============================================================
    @staticmethod
    def _row_to_record(d: Dict[str, Any]) -> BacktestRecord:
        cfg = StrategyConfig.from_dict(json.loads(d["config_json"]))
        return BacktestRecord(
            id=d["id"],
            name=d["name"],
            config=cfg,
            metrics=json.loads(d["metrics_json"]),
            trade_count=d["trade_count"],
            trades=json.loads(d.get("trades_json") or "[]"),
            equity_curve=json.loads(d.get("equity_curve") or "[]"),
            created_at=d.get("created_at"),
            deployed=bool(d.get("deployed")),
            deployed_at=d.get("deployed_at"),
            notes=d.get("notes", ""),
        )
