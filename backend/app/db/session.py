from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def init_engine(database_url: str) -> Engine:
    """(Re)initialise the global engine. Tests call this with a temp database."""
    global _engine, _factory
    if database_url.startswith("sqlite:///"):
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 30},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    from app.db.migrate import migrate

    migrate(engine, database_url)  # additive; backs up an existing database first
    Base.metadata.create_all(engine)
    if _engine is not None:
        _engine.dispose()
    _engine = engine
    _factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("database engine not initialised")
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Short transaction: commit on success, roll back on error."""
    if _factory is None:
        raise RuntimeError("database engine not initialised")
    session = _factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency. Always declare it with ``scope="function"`` so the commit
    happens before the response is sent: a client that receives success must find the
    data committed, and a failed commit must surface as an error, not a lost write."""
    with session_scope() as session:
        yield session
