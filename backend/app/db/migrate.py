"""Additive schema migration with a backup.

P1 only adds tables. Before creating missing tables in an existing SQLite
database, copy the database file (after a WAL checkpoint) to
``<name>.backup-<UTC timestamp>``. Existing tables, rows and audit history are
never dropped or rewritten.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

import app.db.models_p1  # noqa: F401  (registers P1 tables on Base.metadata)
from app.db.models import Base


def missing_tables(engine: Engine) -> list[str]:
    existing = set(inspect(engine).get_table_names())
    return sorted(name for name in Base.metadata.tables if name not in existing)


def backup_sqlite(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = path.with_name(f"{path.name}.backup-{stamp}")
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        dest = sqlite3.connect(target)
        try:
            conn.backup(dest)
        finally:
            dest.close()
    finally:
        conn.close()
    return target


def migrate(engine: Engine, database_url: str) -> dict:
    """Create missing tables; back up first when the database already has tables."""
    missing = missing_tables(engine)
    report: dict = {"created_tables": missing, "backup": None}
    if not missing:
        return report
    has_existing = bool(inspect(engine).get_table_names())
    if has_existing and database_url.startswith("sqlite:///"):
        db_path = Path(database_url.removeprefix("sqlite:///"))
        if db_path.is_file():
            report["backup"] = str(backup_sqlite(db_path))
    Base.metadata.create_all(engine)
    return report


__all__ = ["migrate", "missing_tables", "backup_sqlite"]
