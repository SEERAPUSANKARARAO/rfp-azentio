from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, event, inspect
from sqlmodel import Session, SQLModel, create_engine

from . import models  # noqa: F401  (registers tables on SQLModel.metadata)
from .config import get_settings

FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunk_fts "
    "USING fts5(text, tokenize='porter unicode61')"
)


def make_engine(path: Path | str) -> Engine:
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    return engine


class StaleSchemaError(RuntimeError):
    """The database file was made by an older version of the app and cannot be used as it is."""


def check_schema(engine: Engine) -> None:
    """create_all adds missing tables but never missing columns, so an old file would fail later with a 500."""
    inspector = inspect(engine)
    for table in SQLModel.metadata.sorted_tables:
        have = {c["name"] for c in inspector.get_columns(table.name)}
        missing = sorted(c.name for c in table.columns if c.name not in have)
        if missing:
            path = engine.url.database
            raise StaleSchemaError(
                f"The database {path} was created by an older version of the app: table '{table.name}' has no "
                f"column {', '.join(missing)}. If it holds nothing you need, stop the backend, delete "
                f"{path}* and start it again."
            )


def init_db(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)
    check_schema(engine)
    with engine.begin() as conn:
        # rowid of each FTS row is the KbChunk.id it indexes
        conn.exec_driver_sql(FTS_DDL)


@lru_cache
def get_engine() -> Engine:
    engine = make_engine(get_settings().db_path)
    init_db(engine)
    return engine


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
