"""
backend/app/db.py

SQLite connection helpers.

Performance tuning applied on every connection:
  - WAL journal mode (better concurrent reads)
  - 32 MB page cache
  - Memory-backed temp store
  - 128 MB mmap for sequential scans
  - synchronous=NORMAL (safe with WAL, faster than FULL)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Generator

from .config import settings

_PERF_PRAGMAS = [
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA cache_size=-32000;",     # 32 MB page cache
    "PRAGMA temp_store=MEMORY;",
    "PRAGMA mmap_size=134217728;",   # 128 MB mmap
    "PRAGMA foreign_keys=ON;",
]


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """
    Open and return a SQLite connection with performance PRAGMAs applied.

    Args:
        db_path: Override the path; defaults to settings.resolved_db_path.

    Returns:
        An open sqlite3.Connection with row_factory set to sqlite3.Row.
    """
    path = Path(db_path) if db_path else settings.resolved_db_path
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    for pragma in _PERF_PRAGMAS:
        conn.execute(pragma)
    return conn


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    FastAPI dependency that yields a connection and ensures it is closed.

    Usage::

        @app.get("/some-route")
        def handler(conn: sqlite3.Connection = Depends(get_db)):
            ...
    """
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
