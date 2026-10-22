"""Topic registry: the single place that binds a topic name to its message model."""

from __future__ import annotations

from enum import StrEnum

from alphadots_contracts.base import BaseMessage
from alphadots_contracts.dead_letter import DeadLetterV1
from alphadots_contracts.events import NormalizedEventV1, RawNewsV1, SentimentScoredV1
from alphadots_contracts.signals import SignalGeneratedV1


class Topic(StrEnum):
    NEWS_RAW = "news.raw.v1"
    FILINGS_RAW = "filings.raw.v1"
    EVENTS_NORMALIZED = "events.normalized.v1"
    SENTIMENT_SCORED = "sentiment.scored.v1"
    MARKET_BARS = "market.bars.v1"
    FEATURES_READY = "features.ready.v1"
    SIGNALS_GENERATED = "signals.generated.v1"
    REPORTS_GENERATED = "reports.generated.v1"
    DEAD_LETTER = "events.dead_letter.v1"


TOPIC_MODELS: dict[Topic, type[BaseMessage]] = {
    Topic.NEWS_RAW: RawNewsV1,
    Topic.EVENTS_NORMALIZED: NormalizedEventV1,
    Topic.SENTIMENT_SCORED: SentimentScoredV1,
    Topic.SIGNALS_GENERATED: SignalGeneratedV1,
    Topic.DEAD_LETTER: DeadLetterV1,
}

# Named by the specification but not produced by any service yet. They are reserved so
# nothing else claims the name; each gets a model in the milestone that implements it.
RESERVED_TOPICS: frozenset[Topic] = frozenset(
    {
        Topic.FILINGS_RAW,  # M1
        Topic.MARKET_BARS,  # M3
        Topic.FEATURES_READY,  # M3
        Topic.REPORTS_GENERATED,  # M4
    }
)


def model_for(topic: str) -> type[BaseMessage]:
    """Return the message model for ``topic`` or raise ``KeyError`` if it has none."""
    return TOPIC_MODELS[Topic(topic)]
