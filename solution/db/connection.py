"""SQLite connection and transaction management helpers."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from solution.db.schema import init_db


def open_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection configured with WAL mode and foreign keys."""
    path = Path(db_path)
    if str(db_path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(db_path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_db(db_path: str | Path, initialize: bool = True) -> Iterator[sqlite3.Connection]:
    """Yield an initialized SQLite connection and close it on exit."""
    conn = open_connection(db_path)
    try:
        if initialize:
            init_db(conn)
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Execute a unit of work atomically, rolling back on error."""
    try:
        conn.execute("BEGIN")
        yield conn
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
