"""The API's read model: idempotent projection writes and point-in-time reads.

Every research read takes ``as_of``. When it is set, a row is visible only if its own
``available_at <= as_of``. That holds for each joined table separately: an event can be
visible while its sentiment score, which became available a moment later, is not.

Dead letters are the one exception. They are operational records stamped with wall-clock
time, not research inputs, so they are not filtered by ``as_of``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Result, and_, delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from alphadots_api import tables as t
from alphadots_common.db import upsert
from alphadots_contracts import (
    DeadLetterV1,
    NormalizedEventV1,
    SentimentScoredV1,
    SignalGeneratedV1,
)

Row = dict[str, Any]
"""A result row keyed by column name. Plain data, so callers never touch SQLAlchemy."""

_SENTIMENT_PREFIX = "s_"
_SENTIMENT_COLUMNS = [
    column.label(f"{_SENTIMENT_PREFIX}{column.name}") for column in t.sentiment.columns
]


def _rows(result: Result[Any]) -> list[Row]:
    return [{str(key): value for key, value in row.items()} for row in result.mappings()]


class ReadModel:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(t.metadata.create_all)

    async def ping(self) -> None:
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    # ------------------------------------------------------------------ writes

    async def put_event(self, event: NormalizedEventV1) -> None:
        row = {
            "event_id": event.event_id,
            "raw_event_id": event.raw_event_id,
            "source": event.source,
            "source_event_id": event.source_event_id,
            "source_url": event.source_url,
            "source_license": event.source_license,
            "payload_ref": event.payload_ref,
            "content_hash": event.content_hash,
            "cluster_id": event.cluster_id,
            "duplicate_of": event.duplicate_of,
            "headline": event.headline,
            "body": event.body,
            "language": event.language,
            "published_at": event.published_at,
            "first_seen_at": event.first_seen_at,
            "ingested_at": event.ingested_at,
            "available_at": event.available_at,
            "instruments": [ref.model_dump(mode="json") for ref in event.instruments],
            "instrument_count": len(event.instruments),
            "resolution_flags": [flag.value for flag in event.resolution_flags],
            "normalizer_version": event.normalizer_version,
            "trace_id": event.trace_id,
            "schema_version": event.schema_version,
        }
        links = [
            {
                "event_id": event.event_id,
                "instrument_id": ref.instrument_id,
                "symbol": ref.symbol,
                "confidence": ref.confidence,
                "available_at": event.available_at,
            }
            for ref in event.instruments
        ]
        async with self._engine.begin() as conn:
            await upsert(conn, t.events, [row], key_columns=["event_id"])
            # Replace the links wholesale so a reprocessed event cannot keep stale ones.
            await conn.execute(
                delete(t.event_instruments).where(t.event_instruments.c.event_id == event.event_id)
            )
            await upsert(
                conn, t.event_instruments, links, key_columns=["event_id", "instrument_id"]
            )

    async def put_sentiment(self, scored: SentimentScoredV1) -> None:
        row = {
            "normalized_event_id": scored.normalized_event_id,
            "scored_event_id": scored.event_id,
            "model_id": scored.model_id,
            "model_version": scored.model_version,
            "calibration_version": scored.calibration_version,
            "score_negative": scored.scores.negative,
            "score_neutral": scored.scores.neutral,
            "score_positive": scored.scores.positive,
            "signed_sentiment": scored.signed_sentiment,
            "entropy": scored.entropy,
            "abstained": scored.abstained,
            "abstain_reason": scored.abstain_reason.value if scored.abstain_reason else None,
            "event_type": scored.event_type.value,
            "materiality": scored.materiality,
            "novelty": scored.novelty,
            "rationale_terms": list(scored.rationale_terms),
            "available_at": scored.available_at,
        }
        async with self._engine.begin() as conn:
            await upsert(conn, t.sentiment, [row], key_columns=["normalized_event_id"])

    async def put_signal(self, signal: SignalGeneratedV1) -> None:
        row = {
            "signal_id": signal.event_id,
            "instrument_id": signal.instrument.instrument_id,
            "symbol": signal.instrument.symbol,
            "horizon": signal.horizon,
            "as_of": signal.as_of,
            "available_at": signal.available_at,
            "score": signal.score,
            "score_normalized": signal.score_normalized,
            "stance": signal.stance.value,
            "abstained": signal.abstained,
            "payload": signal.model_dump(mode="json"),
        }
        async with self._engine.begin() as conn:
            await upsert(conn, t.signals, [row], key_columns=["signal_id"])

    async def put_dead_letter(self, dead: DeadLetterV1) -> None:
        row = {
            "dlq_id": dead.event_id,
            "service": dead.service,
            "reason": dead.reason.value,
            "original_topic": dead.original_topic,
            "original_key": dead.original_key,
            "error_detail": dead.error_detail,
            "failed_at": dead.failed_at,
            "trace_id": dead.trace_id,
        }
        async with self._engine.begin() as conn:
            await upsert(conn, t.dead_letters, [row], key_columns=["dlq_id"])

    # ------------------------------------------------------------------- reads

    async def feed_bounds(self) -> tuple[datetime | None, datetime | None]:
        """The feed's event-time range: earliest event, and the latest thing of any kind.

        The upper bound spans events, scores and signals, because a score becomes
        available slightly after its event. Using the event time alone would hide the
        newest score from every "latest" view.
        """
        async with self._engine.connect() as conn:
            earliest = (await conn.execute(select(func.min(t.events.c.available_at)))).scalar()
            latest_values = [
                (await conn.execute(select(func.max(table.c.available_at)))).scalar()
                for table in (t.events, t.sentiment, t.signals)
            ]
        present = [value for value in latest_values if value is not None]
        return earliest, max(present) if present else None

    @staticmethod
    def _events_with_sentiment(as_of: datetime | None) -> Any:
        join_on = t.sentiment.c.normalized_event_id == t.events.c.event_id
        if as_of is not None:
            join_on = and_(join_on, t.sentiment.c.available_at <= as_of)
        query = select(t.events, *_SENTIMENT_COLUMNS).select_from(
            t.events.outerjoin(t.sentiment, join_on)
        )
        if as_of is not None:
            query = query.where(t.events.c.available_at <= as_of)
        return query

    async def list_events(
        self,
        *,
        instrument_id: str | None,
        as_of: datetime | None,
        limit: int,
        cursor: tuple[datetime, str] | None,
        include_duplicates: bool,
    ) -> list[Row]:
        """Newest first by (available_at, event_id); pass the last row back as ``cursor``."""
        query = self._events_with_sentiment(as_of)
        if instrument_id is not None:
            query = query.where(
                t.events.c.event_id.in_(
                    select(t.event_instruments.c.event_id).where(
                        t.event_instruments.c.instrument_id == instrument_id
                    )
                )
            )
        if not include_duplicates:
            query = query.where(t.events.c.duplicate_of.is_(None))
        if cursor is not None:
            cursor_at, cursor_id = cursor
            query = query.where(
                or_(
                    t.events.c.available_at < cursor_at,
                    and_(t.events.c.available_at == cursor_at, t.events.c.event_id < cursor_id),
                )
            )
        query = query.order_by(t.events.c.available_at.desc(), t.events.c.event_id.desc()).limit(
            limit
        )
        async with self._engine.connect() as conn:
            return _rows(await conn.execute(query))

    async def get_event(self, event_id: str, as_of: datetime | None) -> Row | None:
        query = self._events_with_sentiment(as_of).where(t.events.c.event_id == event_id)
        async with self._engine.connect() as conn:
            return next(iter(_rows(await conn.execute(query))), None)

    async def cluster_members(self, cluster_id: str, as_of: datetime | None) -> list[Row]:
        query = select(
            t.events.c.event_id,
            t.events.c.source,
            t.events.c.source_event_id,
            t.events.c.first_seen_at,
            t.events.c.available_at,
            t.events.c.duplicate_of,
        ).where(t.events.c.cluster_id == cluster_id)
        if as_of is not None:
            query = query.where(t.events.c.available_at <= as_of)
        query = query.order_by(t.events.c.available_at, t.events.c.event_id)
        async with self._engine.connect() as conn:
            return _rows(await conn.execute(query))

    async def sentiment_points(
        self, instrument_id: str, *, start: datetime, as_of: datetime, limit: int
    ) -> list[Row]:
        """Scored events for an instrument with ``start < sentiment.available_at <= as_of``,
        oldest first, keeping the most recent ``limit``."""
        query = (
            select(
                t.events.c.event_id,
                t.events.c.headline,
                t.events.c.source,
                t.events.c.published_at,
                t.events.c.duplicate_of,
                t.event_instruments.c.confidence.label("relevance"),
                *_SENTIMENT_COLUMNS,
            )
            .select_from(
                t.events.join(
                    t.event_instruments, t.event_instruments.c.event_id == t.events.c.event_id
                ).join(t.sentiment, t.sentiment.c.normalized_event_id == t.events.c.event_id)
            )
            .where(
                t.event_instruments.c.instrument_id == instrument_id,
                t.events.c.available_at <= as_of,
                t.sentiment.c.available_at > start,
                t.sentiment.c.available_at <= as_of,
            )
            .order_by(t.sentiment.c.available_at.desc(), t.events.c.event_id.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = _rows(await conn.execute(query))
        return list(reversed(rows))

    async def signal_history(
        self, instrument_id: str, horizon: str, *, as_of: datetime | None, limit: int
    ) -> list[Row]:
        """Signals available by ``as_of``, oldest first, keeping the most recent ``limit``."""
        query = select(t.signals).where(
            t.signals.c.instrument_id == instrument_id, t.signals.c.horizon == horizon
        )
        if as_of is not None:
            query = query.where(t.signals.c.available_at <= as_of)
        query = query.order_by(t.signals.c.as_of.desc(), t.signals.c.signal_id.desc()).limit(limit)
        async with self._engine.connect() as conn:
            rows = _rows(await conn.execute(query))
        return list(reversed(rows))

    async def recent_signals(self, as_of: datetime | None, limit: int) -> list[Row]:
        """Compact signal rows across all instruments, oldest first (for overview cards)."""
        query = select(
            t.signals.c.signal_id,
            t.signals.c.instrument_id,
            t.signals.c.horizon,
            t.signals.c.as_of,
            t.signals.c.available_at,
            t.signals.c.score,
            t.signals.c.score_normalized,
            t.signals.c.stance,
            t.signals.c.abstained,
        )
        if as_of is not None:
            query = query.where(t.signals.c.available_at <= as_of)
        query = query.order_by(t.signals.c.as_of.desc(), t.signals.c.signal_id.desc()).limit(limit)
        async with self._engine.connect() as conn:
            rows = _rows(await conn.execute(query))
        return list(reversed(rows))

    async def instrument_event_counts(self, as_of: datetime | None) -> dict[str, int]:
        query = select(t.event_instruments.c.instrument_id, func.count()).group_by(
            t.event_instruments.c.instrument_id
        )
        if as_of is not None:
            query = query.where(t.event_instruments.c.available_at <= as_of)
        async with self._engine.connect() as conn:
            return {row[0]: int(row[1]) for row in await conn.execute(query)}

    async def pipeline_counts(self, as_of: datetime | None) -> dict[str, int]:
        def visible(table: Any) -> Any:
            return table.c.available_at <= as_of if as_of is not None else text("1=1")

        queries = {
            "events": select(func.count()).select_from(t.events).where(visible(t.events)),
            "duplicates": select(func.count())
            .select_from(t.events)
            .where(visible(t.events), t.events.c.duplicate_of.is_not(None)),
            "unresolved": select(func.count())
            .select_from(t.events)
            .where(visible(t.events), t.events.c.instrument_count == 0),
            "scored": select(func.count()).select_from(t.sentiment).where(visible(t.sentiment)),
            "sentiment_abstained": select(func.count())
            .select_from(t.sentiment)
            .where(visible(t.sentiment), t.sentiment.c.abstained.is_(True)),
            "signals": select(func.count()).select_from(t.signals).where(visible(t.signals)),
            "signals_abstained": select(func.count())
            .select_from(t.signals)
            .where(visible(t.signals), t.signals.c.abstained.is_(True)),
            "dead_letters": select(func.count()).select_from(t.dead_letters),
        }
        counts: dict[str, int] = {}
        async with self._engine.connect() as conn:
            for name, query in queries.items():
                counts[name] = int((await conn.execute(query)).scalar_one())
        return counts

    async def list_dead_letters(self, limit: int) -> list[Row]:
        query = (
            select(t.dead_letters)
            .order_by(t.dead_letters.c.failed_at.desc(), t.dead_letters.c.dlq_id)
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            return _rows(await conn.execute(query))
