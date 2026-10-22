"""Dedup store owned by the preprocessor: content fingerprint -> canonical event.

``claim`` is the only operation. The first event to claim a fingerprint becomes canonical;
every later claimant is told who that was. Claiming is idempotent, so a redelivered
canonical event is still canonical and a redelivered copy is still a copy.

Known limit: "first" means first *processed*. A copy that was published earlier but
arrives later stays a copy. Reordering by event time needs a watermark policy (M1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sqlalchemy import Column, MetaData, String, Table, select
from sqlalchemy.ext.asyncio import AsyncEngine

from alphadots_common.db import UtcDateTime, upsert

metadata = MetaData()

content_seen = Table(
    "pre_content_seen",
    metadata,
    Column("fingerprint", String(64), primary_key=True),
    Column("canonical_event_id", String(128), nullable=False),
    Column("first_available_at", UtcDateTime(), nullable=False),
)


class DedupStore(Protocol):
    async def claim(self, fingerprint: str, event_id: str, available_at: datetime) -> str:
        """Return the canonical event_id for ``fingerprint``, claiming it if unclaimed."""
        ...


class InMemoryDedupStore:
    def __init__(self) -> None:
        self._canonical: dict[str, str] = {}

    async def claim(self, fingerprint: str, event_id: str, available_at: datetime) -> str:
        return self._canonical.setdefault(fingerprint, event_id)


class SqlDedupStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    async def claim(self, fingerprint: str, event_id: str, available_at: datetime) -> str:
        async with self._engine.begin() as conn:
            # Insert-if-absent then read back: correct under concurrent claimants because
            # the primary key decides the winner, not the order of the reads.
            await upsert(
                conn,
                content_seen,
                [
                    {
                        "fingerprint": fingerprint,
                        "canonical_event_id": event_id,
                        "first_available_at": available_at,
                    }
                ],
                key_columns=["fingerprint"],
                update=False,
            )
            result = await conn.execute(
                select(content_seen.c.canonical_event_id).where(
                    content_seen.c.fingerprint == fingerprint
                )
            )
            return str(result.scalar_one())
