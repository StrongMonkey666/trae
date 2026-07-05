"""选股历史记录（SQLite 存储）。"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger
from .schema import SelectorSpec

logger = get_logger(__name__)


class SelectorHistory:
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
                CREATE TABLE IF NOT EXISTS selector_record (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    name          TEXT,
                    natural_lang  TEXT,
                    spec_json     TEXT,
                    result_codes  TEXT,         -- 逗号分隔
                    result_count  INTEGER,
                    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS selector_template_nl (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT,
                    description TEXT,
                    natural_lang TEXT
                );
                """
            )

    def save(
        self,
        name: str,
        spec: SelectorSpec,
        result_codes: List[str],
        natural_lang: str = "",
    ) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO selector_record
                    (name, natural_lang, spec_json, result_codes, result_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    name, natural_lang, spec.to_json(),
                    ",".join(sorted(set(result_codes))),
                    len(set(result_codes)),
                ),
            )
            return cur.lastrowid

    def list_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, name, natural_lang, spec_json, result_codes,
                       result_count, created_at
                FROM selector_record
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, record_id: int) -> Optional[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM selector_record WHERE id=?", (record_id,)
            ).fetchone()
        return dict(row) if row else None

    def save_template_nl(self, name: str, description: str, natural_lang: str) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO selector_template_nl (name, description, natural_lang) "
                "VALUES (?, ?, ?)",
                (name, description, natural_lang),
            )
            return cur.lastrowid

    def list_template_nl(self) -> List[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM selector_template_nl ORDER BY id DESC"
            ).fetchall()
        return [dict(r) for r in rows]
