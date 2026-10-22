"""Scored-event store owned by the signal engine.

One row per (scored event, instrument). ``between`` is the only read. When a signal is
computed, the engine asks for rows up to its decision time and no further, so rows with
``available_at > as_of`` are never loaded as inputs and cannot leak in further up the stack.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import Boolean, Column, Float, Index, MetaData, String, Table, select
from sqlalchemy.ext.asyncio import AsyncEngine

from alphadots_common.db import UtcDateTime, upsert

metadata = MetaData()

scored_events = Table(
    "sig_scored_events",
    metadata,
    Column("scored_event_id", String(128), primary_key=True),
    Column("instrument_id", String(64), primary_key=True),
    Column("symbol", String(16), nullable=False),
    Column("normalized_event_id", String(128), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("source", String(64), nullable=False),
    Column("available_at", UtcDateTime(), nullable=False),
    Column("signed_sentiment", Float, nullable=False),
    Column("relevance", Float, nullable=False),
    Column("materiality", Float, nullable=False),
    Column("abstained", Boolean, nullable=False),
    Column("is_duplicate", Boolean, nullable=False),
    Index("ix_sig_scored_events_instrument_available", "instrument_id", "available_at"),
)


@dataclass(frozen=True, slots=True)
class ScoredRow:
    scored_event_id: str
    instrument_id: str
    symbol: str
    normalized_event_id: str
    trace_id: str
    source: str
    available_at: datetime
    signed_sentiment: float
    relevance: float
    materiality: float
    abstained: bool
    is_duplicate: bool


class ScoredEventStore(Protocol):
    async def put(self, rows: list[ScoredRow]) -> None:
        """Idempotently store ``rows`` keyed by (scored_event_id, instrument_id)."""
        ...

    async def between(
        self, instrument_id: str, *, start: datetime, end: datetime
    ) -> list[ScoredRow]:
        """Rows for ``instrument_id`` with ``start <= available_at <= end``."""
        ...


class InMemoryScoredEventStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], ScoredRow] = {}

    async def put(self, rows: list[ScoredRow]) -> None:
        for row in rows:
            self._rows[(row.scored_event_id, row.instrument_id)] = row

    async def between(
        self, instrument_id: str, *, start: datetime, end: datetime
    ) -> list[ScoredRow]:
        return [
            row
            for row in self._rows.values()
            if row.instrument_id == instrument_id and start <= row.available_at <= end
        ]


class SqlScoredEventStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    async def put(self, rows: list[ScoredRow]) -> None:
        values = [
            {column.name: getattr(row, column.name) for column in scored_events.columns}
            for row in rows
        ]
        async with self._engine.begin() as conn:
            await upsert(
                conn,
                scored_events,
                values,
                key_columns=["scored_event_id", "instrument_id"],
            )

    async def between(
        self, instrument_id: str, *, start: datetime, end: datetime
    ) -> list[ScoredRow]:
        query = select(scored_events).where(
            scored_events.c.instrument_id == instrument_id,
            scored_events.c.available_at >= start,
            scored_events.c.available_at <= end,
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(query)
            return [ScoredRow(**row) for row in result.mappings()]
