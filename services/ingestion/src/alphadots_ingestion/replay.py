"""Publish adapter output to the bus, optionally paced by event time."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

import structlog

from alphadots_common.bus.base import EventBus
from alphadots_common.ids import derive_id, trace_id_for
from alphadots_common.metrics import DLQ_EVENTS, INGEST_EVENTS, INGEST_LAG_SECONDS
from alphadots_common.timeutils import utc_now
from alphadots_common.worker import Outbound, publish
from alphadots_contracts import (
    MAX_DLQ_ERROR_CHARS,
    MAX_DLQ_PAYLOAD_CHARS,
    DeadLetterReason,
    DeadLetterV1,
    RawNewsV1,
    Topic,
)
from alphadots_ingestion.adapters import PoisonRecord, SourceAdapter

SERVICE = "ingestion"
_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReplayStats:
    published: int
    quarantined: int


def _dead_letter(poison: PoisonRecord, now: datetime) -> DeadLetterV1:
    dlq_id = derive_id("dlq", SERVICE, poison.payload_ref)
    return DeadLetterV1(
        event_id=dlq_id,
        trace_id=trace_id_for(dlq_id),
        available_at=now,
        service=SERVICE,
        reason=DeadLetterReason.POISON_SOURCE_RECORD,
        original_topic=Topic.NEWS_RAW.value,
        original_key=poison.payload_ref,
        error_detail=poison.error[:MAX_DLQ_ERROR_CHARS],
        payload=poison.raw[:MAX_DLQ_PAYLOAD_CHARS],
        payload_truncated=len(poison.raw) > MAX_DLQ_PAYLOAD_CHARS,
        failed_at=now,
    )


async def replay(
    adapter: SourceAdapter,
    bus: EventBus,
    *,
    speed: float = 0.0,
    max_gap_seconds: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], datetime] = utc_now,
) -> ReplayStats:
    """Publish every adapter record in order.

    ``speed`` is the event-time acceleration factor: 1.0 replays at the recorded pace,
    3600.0 compresses an hour into a second, and 0 disables pacing entirely. Gaps are
    capped at ``max_gap_seconds`` of wall time so a demo never stalls overnight. Pacing
    changes only *when* a message is sent, never its content.
    """
    if speed < 0:
        raise ValueError("speed must be >= 0")
    published = 0
    quarantined = 0
    previous_seen: datetime | None = None
    for record in adapter.records():
        if isinstance(record, PoisonRecord):
            await publish(
                bus, Outbound(Topic.DEAD_LETTER, record.payload_ref, _dead_letter(record, clock()))
            )
            INGEST_EVENTS.labels(source=record.source, status="quarantined").inc()
            DLQ_EVENTS.labels(
                service=SERVICE, reason=DeadLetterReason.POISON_SOURCE_RECORD.value
            ).inc()
            _log.warning("source_record_quarantined", payload_ref=record.payload_ref)
            quarantined += 1
            continue

        if speed > 0 and previous_seen is not None:
            gap = (record.first_seen_at - previous_seen).total_seconds() / speed
            if gap > 0:
                await sleep(min(gap, max_gap_seconds))
        previous_seen = record.first_seen_at
        await _publish_raw(bus, record)
        published += 1
    return ReplayStats(published=published, quarantined=quarantined)


async def _publish_raw(bus: EventBus, event: RawNewsV1) -> None:
    await publish(bus, Outbound(Topic.NEWS_RAW, event.event_id, event))
    INGEST_EVENTS.labels(source=event.source, status="published").inc()
    INGEST_LAG_SECONDS.labels(source=event.source).set(
        (event.first_seen_at - event.published_at).total_seconds()
    )
