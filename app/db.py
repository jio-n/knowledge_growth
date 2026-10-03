"""SQLite persistence layer (stdlib sqlite3, no ORM).

Schema documented in docs/architecture/data_model.md — keep both in sync.
One connection per request via `get_db()`; WAL mode for concurrent reads.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from .config import DB_PATH, ensure_dirs
from .migrations import BASELINE_SCHEMA, run_migrations

# Compatibility alias; the v1 schema is immutable and owned by migrations.
SCHEMA = BASELINE_SCHEMA


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def get_db() -> sqlite3.Connection:
    ensure_dirs()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db() -> None:
    con = get_db()
    try:
        run_migrations(con)
    finally:
        con.close()


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]
