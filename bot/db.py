"""Shared SQLite database for bot state.

A single ``plexinbox.db`` on the data volume holds the dedupe archive, per-user
settings, and per-user language preferences. Each store opens its own lazy
connection to the same file; SQLite's locking keeps the (sequential, single
process) access safe.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_FILENAME = "plexinbox.db"


def db_path(data_dir: str | Path) -> Path:
    """Path to the shared database file under ``data_dir``."""
    return Path(data_dir) / DB_FILENAME


def connect(data_dir: str | Path) -> sqlite3.Connection:
    """Open a connection to the shared database, creating ``data_dir`` as needed."""
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(d / DB_FILENAME))
