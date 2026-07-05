"""存储层。

- SQLite：股票元信息、数据源状态、运行日志、配置
- HDF5：大规模时序数据（K 线、财务指标）

HDF5 存储结构：
    /kline/{code}            -> DataFrame(date, open, high, low, close, volume, amount, adj_factor, suspended)
    /financial/{code}        -> DataFrame(...)
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from ..utils.exceptions import StorageError
from ..utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================
# SQLite
# ============================================================
class SqliteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.path), detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._conn() as conn:
            c = conn.cursor()
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS stock (
                    code        TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    market      TEXT,
                    industry    TEXT,
                    list_date   DATE,
                    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS data_source (
                    name        TEXT PRIMARY KEY,
                    enabled     INTEGER NOT NULL DEFAULT 1,
                    last_ok     TIMESTAMP,
                    last_fail   TIMESTAMP,
                    note        TEXT
                );

                CREATE TABLE IF NOT EXISTS sync_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    code        TEXT,
                    source      TEXT,
                    freq        TEXT,
                    start_date  DATE,
                    end_date    DATE,
                    rows        INTEGER,
                    ok          INTEGER,
                    error       TEXT,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )

    # ---------- stock ----------
    def upsert_stocks(self, rows: Iterable[Dict[str, Any]]) -> int:
        n = 0
        with self._lock, self._conn() as conn:
            c = conn.cursor()
            for r in rows:
                c.execute(
                    """
                    INSERT INTO stock (code, name, market, industry, list_date, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(code) DO UPDATE SET
                        name=excluded.name,
                        market=excluded.market,
                        industry=COALESCE(excluded.industry, stock.industry),
                        list_date=COALESCE(excluded.list_date, stock.list_date),
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        str(r["code"]).zfill(6),
                        r.get("name", ""),
                        r.get("market", ""),
                        r.get("industry", ""),
                        r.get("list_date"),
                    ),
                )
                n += 1
        return n

    def list_stocks(self, market: Optional[str] = None) -> pd.DataFrame:
        with self._lock, self._conn() as conn:
            if market:
                df = pd.read_sql_query(
                    "SELECT * FROM stock WHERE market=? ORDER BY code", conn, params=(market,)
                )
            else:
                df = pd.read_sql_query("SELECT * FROM stock ORDER BY code", conn)
        return df

    def get_stock(self, code: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM stock WHERE code=?", (str(code).zfill(6),)
            ).fetchone()
        return dict(row) if row else None

    # ---------- data_source ----------
    def update_source_status(self, name: str, ok: bool, note: str = "") -> None:
        with self._lock, self._conn() as conn:
            c = conn.cursor()
            if ok:
                c.execute(
                    """
                    INSERT INTO data_source (name, enabled, last_ok, note)
                    VALUES (?, 1, CURRENT_TIMESTAMP, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        last_ok=CURRENT_TIMESTAMP, note=excluded.note
                    """,
                    (name, note),
                )
            else:
                c.execute(
                    """
                    INSERT INTO data_source (name, enabled, last_fail, note)
                    VALUES (?, 1, CURRENT_TIMESTAMP, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        last_fail=CURRENT_TIMESTAMP, note=excluded.note
                    """,
                    (name, note),
                )

    def source_status(self) -> pd.DataFrame:
        with self._lock, self._conn() as conn:
            return pd.read_sql_query("SELECT * FROM data_source", conn)

    # ---------- sync_log ----------
    def log_sync(
        self,
        source: str,
        freq: str,
        start_date: date,
        end_date: date,
        rows: int,
        ok: bool,
        code: str = "",
        error: str = "",
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO sync_log
                    (code, source, freq, start_date, end_date, rows, ok, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    code, source, freq, start_date, end_date, rows,
                    1 if ok else 0, error,
                ),
            )

    # ---------- config ----------
    def get_config(self, key: str, default: Any = None) -> Any:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_config(self, key: str, value: Any) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO config (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, str(value)),
            )


# ============================================================
# HDF5
# ============================================================
class Hdf5Store:
    """HDF5 时序数据存储。"""

    KLINE_GROUP = "kline"
    FINANCIAL_GROUP = "financial"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---------- K 线 ----------
    def save_kline(self, code: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        with self._lock:
            with pd.HDFStore(self.path, mode="a", complevel=4) as store:
                key = f"/{self.KLINE_GROUP}/{self._safe(code)}"
                if key in store:
                    existing = store[key]
                    merged = self._merge_kline(existing, df)
                else:
                    merged = df
                store.put(key, merged, format="table", data_columns=["date"])

    def load_kline(self, code: str) -> pd.DataFrame:
        if not self.path.is_file():
            return pd.DataFrame()
        with self._lock:
            with pd.HDFStore(self.path, mode="r") as store:
                key = f"/{self.KLINE_GROUP}/{self._safe(code)}"
                if key not in store:
                    return pd.DataFrame()
                df = store[key]
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df

    def has_kline(self, code: str) -> bool:
        if not self.path.is_file():
            return False
        with self._lock:
            with pd.HDFStore(self.path, mode="r") as store:
                return f"/{self.KLINE_GROUP}/{self._safe(code)}" in store

    def list_kline_codes(self) -> List[str]:
        if not self.path.is_file():
            return []
        with self._lock:
            with pd.HDFStore(self.path, mode="r") as store:
                keys = [
                    k.split("/")[-1]
                    for k in store.keys()
                    if k.startswith(f"/{self.KLINE_GROUP}/")
                ]
        return keys

    @staticmethod
    def _merge_kline(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
        """合并新旧 K 线（去重，按日期升序）。"""
        ex = existing.copy()
        ex["date"] = pd.to_datetime(ex["date"])
        nw = new.copy()
        nw["date"] = pd.to_datetime(nw["date"])
        all_df = pd.concat([ex, nw], ignore_index=True)
        all_df = all_df.drop_duplicates(subset=["date"], keep="last")
        return all_df.sort_values("date").reset_index(drop=True)

    # ---------- 财务 ----------
    def save_financial(self, code: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        df = df.copy()
        df["report_date"] = pd.to_datetime(df["report_date"])
        with self._lock:
            with pd.HDFStore(self.path, mode="a", complevel=4) as store:
                key = f"/{self.FINANCIAL_GROUP}/{self._safe(code)}"
                if key in store:
                    existing = store[key]
                    merged = self._merge_financial(existing, df)
                else:
                    merged = df
                store.put(key, merged, format="table", data_columns=["report_date"])

    def load_financial(self, code: str) -> pd.DataFrame:
        if not self.path.is_file():
            return pd.DataFrame()
        with self._lock:
            with pd.HDFStore(self.path, mode="r") as store:
                key = f"/{self.FINANCIAL_GROUP}/{self._safe(code)}"
                if key not in store:
                    return pd.DataFrame()
                df = store[key]
        df = df.copy()
        df["report_date"] = pd.to_datetime(df["report_date"]).dt.date
        return df

    @staticmethod
    def _merge_financial(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
        ex = existing.copy()
        ex["report_date"] = pd.to_datetime(ex["report_date"])
        nw = new.copy()
        nw["report_date"] = pd.to_datetime(nw["report_date"])
        all_df = pd.concat([ex, nw], ignore_index=True)
        all_df = all_df.drop_duplicates(subset=["report_date"], keep="last")
        return all_df.sort_values("report_date").reset_index(drop=True)

    @staticmethod
    def _safe(code: str) -> str:
        return str(code).zfill(6).replace("/", "_")


# ============================================================
# 顶层存储：组合 SQLite + HDF5
# ============================================================
class DataStore:
    """组合存储，对外屏蔽底层差异。"""

    def __init__(self, sqlite_path: str | Path, hdf5_path: str | Path) -> None:
        self.sqlite = SqliteStore(sqlite_path)
        self.hdf5 = Hdf5Store(hdf5_path)
        logger.info(
            "DataStore 初始化完成: sqlite=%s, hdf5=%s", sqlite_path, hdf5_path
        )
