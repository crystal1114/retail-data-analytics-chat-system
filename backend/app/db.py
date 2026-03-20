"""
backend/app/db.py

SQLite connection helpers.

Each request should call get_connection() to obtain a dedicated connection
and close it when done.  FastAPI dependency get_db() handles this lifecycle.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Generator

from .config import settings


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """
    Open and return a SQLite connection.

    Args:
        db_path: Override the path; defaults to settings.resolved_db_path.

    Returns:
        An open sqlite3.Connection with row_factory set to sqlite3.Row.
    """
    path = Path(db_path) if db_path else settings.resolved_db_path
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Enable WAL for better concurrent read performance
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
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
