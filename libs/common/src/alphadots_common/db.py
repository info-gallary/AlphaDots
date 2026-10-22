"""Async SQLAlchemy helpers shared by services that own a store.

PostgreSQL is the deployed database. SQLite is supported so unit tests and the no-Docker
dev stack need nothing installed. Requires the ``alphadots-common[db]`` extra.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, Dialect, Table, event
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import make_url
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

# A store that is unreachable is not the message's fault. Workers pass this to
# ``StreamWorker(infrastructure_errors=...)`` so such failures crash-and-redeliver
# instead of sending good records to the dead-letter topic.
DB_INFRASTRUCTURE_ERRORS: tuple[type[Exception], ...] = (OperationalError, InterfaceError, OSError)


class UtcDateTime(TypeDecorator[datetime]):
    """A timestamp column that only accepts and only returns timezone-aware UTC values.

    SQLite has no timezone-aware type, so values are stored as naive UTC there and the
    zone is re-attached on read. Naive inputs are rejected rather than guessed.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetime cannot be stored; attach a timezone explicitly")
        as_utc = value.astimezone(UTC)
        return as_utc.replace(tzinfo=None) if dialect.name == "sqlite" else as_utc

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def make_engine(database_url: str) -> AsyncEngine:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        if url.database in (None, "", ":memory:"):
            # One shared connection, otherwise every checkout sees a fresh empty database.
            return create_async_engine(database_url, poolclass=StaticPool)
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_async_engine(database_url)

        @event.listens_for(engine.sync_engine, "connect")
        def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
            # WAL lets readers proceed while a stage writes; the timeout absorbs brief locks.
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        return engine
    return create_async_engine(database_url, pool_pre_ping=True)


async def upsert(
    conn: AsyncConnection,
    table: Table,
    rows: Sequence[Mapping[str, Any]],
    *,
    key_columns: Sequence[str],
    update: bool = True,
) -> None:
    """Insert ``rows``; on a key conflict overwrite the non-key columns or do nothing.

    This is the idempotent effect behind at-least-once delivery: replaying a message
    rewrites the same row with the same values.
    """
    if not rows:
        return
    dialect = conn.dialect.name
    if dialect == "postgresql":
        statement: Any = postgresql.insert(table).values(list(rows))
    elif dialect == "sqlite":
        statement = sqlite.insert(table).values(list(rows))
    else:
        raise NotImplementedError(f"upsert is not implemented for dialect {dialect!r}")

    update_columns = [c.name for c in table.columns if c.name not in key_columns] if update else []
    if update_columns:
        statement = statement.on_conflict_do_update(
            index_elements=list(key_columns),
            set_={name: statement.excluded[name] for name in update_columns},
        )
    else:
        statement = statement.on_conflict_do_nothing(index_elements=list(key_columns))
    await conn.execute(statement)
