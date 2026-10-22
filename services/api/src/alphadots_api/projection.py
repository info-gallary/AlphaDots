"""Stream → read model projection.

Each handler is an idempotent upsert keyed by the message's deterministic ID, so the
projection tolerates redelivery and can be rebuilt by replaying the topics from the start.
"""

from __future__ import annotations

from collections.abc import Sequence

from alphadots_api.read_model import ReadModel
from alphadots_common.bus.base import EventBus
from alphadots_common.db import DB_INFRASTRUCTURE_ERRORS
from alphadots_common.worker import Outbound, RetryPolicy, Route, StreamWorker
from alphadots_contracts import (
    DeadLetterV1,
    NormalizedEventV1,
    SentimentScoredV1,
    SignalGeneratedV1,
    Topic,
)

SERVICE = "api"
CONSUMER_GROUP = "alphadots.api.projection.v1"


def build_projection_worker(
    *, bus: EventBus, read_model: ReadModel, retry: RetryPolicy | None = None
) -> StreamWorker:
    async def on_event(event: NormalizedEventV1) -> Sequence[Outbound]:
        await read_model.put_event(event)
        return []

    async def on_sentiment(scored: SentimentScoredV1) -> Sequence[Outbound]:
        await read_model.put_sentiment(scored)
        return []

    async def on_signal(signal: SignalGeneratedV1) -> Sequence[Outbound]:
        await read_model.put_signal(signal)
        return []

    async def on_dead_letter(dead: DeadLetterV1) -> Sequence[Outbound]:
        await read_model.put_dead_letter(dead)
        return []

    return StreamWorker(
        service=SERVICE,
        bus=bus,
        group=CONSUMER_GROUP,
        routes={
            Topic.EVENTS_NORMALIZED: Route(NormalizedEventV1, on_event),
            Topic.SENTIMENT_SCORED: Route(SentimentScoredV1, on_sentiment),
            Topic.SIGNALS_GENERATED: Route(SignalGeneratedV1, on_signal),
            Topic.DEAD_LETTER: Route(DeadLetterV1, on_dead_letter),
        },
        retry=retry,
        infrastructure_errors=DB_INFRASTRUCTURE_ERRORS,
    )
