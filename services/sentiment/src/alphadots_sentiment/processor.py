"""Score one normalised event with the lexicon baseline."""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import timedelta

from alphadots_common.bus.base import EventBus
from alphadots_common.ids import derive_id
from alphadots_common.metrics import MODEL_ABSTENTIONS, MODEL_INFERENCE_SECONDS
from alphadots_common.worker import Outbound, RetryPolicy, Route, StreamWorker
from alphadots_contracts import (
    AbstainReason,
    NormalizedEventV1,
    PolarityScores,
    SentimentScoredV1,
    Topic,
)
from alphadots_sentiment.event_types import MATERIALITY_PRIOR, classify_event_type
from alphadots_sentiment.lexicon import (
    CALIBRATION_VERSION,
    MODEL_ID,
    MODEL_VERSION,
    UNIFORM_SCORES,
    normalized_entropy,
    polarity_scores,
    score_text,
    tokenize,
)

SERVICE = "sentiment"
CONSUMER_GROUP = "alphadots.sentiment.v1"
SUPPORTED_LANGUAGES = frozenset({"en"})
# Above this the positive and negative evidence roughly cancel; say so instead of guessing.
HIGH_ENTROPY_THRESHOLD = 0.95


class SentimentProcessor:
    def __init__(self, *, stage_allowance: timedelta) -> None:
        self._stage_allowance = stage_allowance

    def score(self, event: NormalizedEventV1) -> SentimentScoredV1:
        started = time.perf_counter()
        text = f"{event.headline}. {event.body}" if event.body else event.headline
        tokens = tokenize(text)
        event_type = classify_event_type(tokens)

        scores: PolarityScores = UNIFORM_SCORES
        terms: tuple[str, ...] = ()
        abstain: AbstainReason | None = None
        if event.language not in SUPPORTED_LANGUAGES:
            abstain = AbstainReason.UNSUPPORTED_LANGUAGE
        else:
            result = score_text(text)
            terms = result.terms
            if result.total_hits == 0:
                abstain = AbstainReason.NO_LEXICON_MATCH
            else:
                scores = polarity_scores(result)
                if normalized_entropy(scores) >= HIGH_ENTROPY_THRESHOLD:
                    abstain = AbstainReason.HIGH_ENTROPY

        MODEL_INFERENCE_SECONDS.labels(model_version=MODEL_VERSION).observe(
            time.perf_counter() - started
        )
        if abstain is not None:
            MODEL_ABSTENTIONS.labels(reason=abstain.value).inc()

        return SentimentScoredV1(
            event_id=derive_id("sent", event.event_id, MODEL_ID),
            trace_id=event.trace_id,
            available_at=event.available_at + self._stage_allowance,
            source=event.source,
            source_event_id=event.source_event_id,
            content_hash=event.content_hash,
            source_url=event.source_url,
            published_at=event.published_at,
            first_seen_at=event.first_seen_at,
            ingested_at=event.ingested_at,
            instruments=event.instruments,
            source_license=event.source_license,
            payload_ref=event.payload_ref,
            normalized_event_id=event.event_id,
            model_id=MODEL_ID,
            model_version=MODEL_VERSION,
            calibration_version=CALIBRATION_VERSION,
            scores=scores,
            signed_sentiment=scores.positive - scores.negative,
            entropy=normalized_entropy(scores),
            abstained=abstain is not None,
            abstain_reason=abstain,
            event_type=event_type,
            materiality=MATERIALITY_PRIOR[event_type],
            # Exact-duplicate novelty only: a copy of a known event carries nothing new.
            novelty=0.0 if event.duplicate_of else 1.0,
            rationale_terms=terms,
        )

    async def handle(self, event: NormalizedEventV1) -> Sequence[Outbound]:
        scored = self.score(event)
        return [Outbound(Topic.SENTIMENT_SCORED, scored.event_id, scored)]


def build_worker(
    *, bus: EventBus, processor: SentimentProcessor, retry: RetryPolicy | None = None
) -> StreamWorker:
    return StreamWorker(
        service=SERVICE,
        bus=bus,
        group=CONSUMER_GROUP,
        routes={Topic.EVENTS_NORMALIZED: Route(NormalizedEventV1, processor.handle)},
        retry=retry,
    )
