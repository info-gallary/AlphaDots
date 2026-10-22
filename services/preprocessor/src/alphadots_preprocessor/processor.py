"""Turn one raw news item into one normalised event."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from alphadots_common.bus.base import EventBus
from alphadots_common.db import DB_INFRASTRUCTURE_ERRORS
from alphadots_common.ids import derive_id
from alphadots_common.worker import Outbound, RejectMessage, RetryPolicy, Route, StreamWorker
from alphadots_contracts import NormalizedEventV1, RawNewsV1, Topic
from alphadots_preprocessor.dedup import DedupStore
from alphadots_preprocessor.entities import EntityResolver
from alphadots_preprocessor.text import clean_text, content_fingerprint

SERVICE = "preprocessor"
CONSUMER_GROUP = "alphadots.preprocessor.v1"
NORMALIZER_VERSION = "norm-0.1.0"
UNDETERMINED_LANGUAGE = "und"
_CLUSTER_ID_HEX_CHARS = 24


class Preprocessor:
    def __init__(
        self, *, resolver: EntityResolver, dedup: DedupStore, stage_allowance: timedelta
    ) -> None:
        self._resolver = resolver
        self._dedup = dedup
        self._stage_allowance = stage_allowance

    async def normalize(self, raw: RawNewsV1) -> NormalizedEventV1:
        headline = clean_text(raw.headline)
        if not headline:
            raise RejectMessage("headline is empty after removing markup and control characters")
        body = clean_text(raw.body) if raw.body else None

        fingerprint = content_fingerprint(headline, body)
        event_id = derive_id("norm", raw.event_id)
        available_at = raw.available_at + self._stage_allowance
        canonical_id = await self._dedup.claim(fingerprint, event_id, available_at)

        resolution = self._resolver.resolve(
            text=f"{headline}\n{body}" if body else headline,
            provider_symbols=raw.provider_symbols,
            # A ticker means whatever it meant on the day the source published.
            as_of=raw.published_at,
        )
        return NormalizedEventV1(
            event_id=event_id,
            trace_id=raw.trace_id,
            available_at=available_at,
            source=raw.source,
            source_event_id=raw.source_event_id,
            content_hash=fingerprint,
            source_url=raw.source_url,
            published_at=raw.published_at,
            first_seen_at=raw.first_seen_at,
            ingested_at=raw.ingested_at,
            instruments=resolution.instruments,
            source_license=raw.source_license,
            payload_ref=raw.payload_ref,
            raw_event_id=raw.event_id,
            headline=headline,
            body=body or None,
            # Trusts the provider's tag. Detection from text is not implemented yet.
            language=(raw.language or UNDETERMINED_LANGUAGE).strip().lower(),
            cluster_id=f"clu_{fingerprint[:_CLUSTER_ID_HEX_CHARS]}",
            duplicate_of=None if canonical_id == event_id else canonical_id,
            resolution_flags=resolution.flags,
            normalizer_version=NORMALIZER_VERSION,
        )

    async def handle(self, raw: RawNewsV1) -> Sequence[Outbound]:
        event = await self.normalize(raw)
        return [Outbound(Topic.EVENTS_NORMALIZED, event.event_id, event)]


def build_worker(
    *, bus: EventBus, preprocessor: Preprocessor, retry: RetryPolicy | None = None
) -> StreamWorker:
    return StreamWorker(
        service=SERVICE,
        bus=bus,
        group=CONSUMER_GROUP,
        routes={Topic.NEWS_RAW: Route(RawNewsV1, preprocessor.handle)},
        retry=retry,
        infrastructure_errors=DB_INFRASTRUCTURE_ERRORS,
    )
