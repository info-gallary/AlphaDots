"""Read-model tables owned by the API service.

Nothing else reads or writes these. They are a projection of the event stream and can be
rebuilt from offset zero at any time, which is why there are no foreign keys: sentiment
for an event may legitimately arrive before the event itself.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Float, Index, Integer, MetaData, String, Table, Text

from alphadots_common.db import UtcDateTime

metadata = MetaData()

events = Table(
    "api_events",
    metadata,
    Column("event_id", String(128), primary_key=True),
    Column("raw_event_id", String(128), nullable=False),
    Column("source", String(64), nullable=False),
    Column("source_event_id", String(256), nullable=False),
    Column("source_url", Text, nullable=False),
    Column("source_license", String(128), nullable=False),
    Column("payload_ref", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("cluster_id", String(64), nullable=False),
    Column("duplicate_of", String(128), nullable=True),
    Column("headline", Text, nullable=False),
    Column("body", Text, nullable=True),
    Column("language", String(16), nullable=False),
    Column("published_at", UtcDateTime(), nullable=False),
    Column("first_seen_at", UtcDateTime(), nullable=False),
    Column("ingested_at", UtcDateTime(), nullable=False),
    Column("available_at", UtcDateTime(), nullable=False),
    Column("instruments", JSON, nullable=False),
    Column("instrument_count", Integer, nullable=False),
    Column("resolution_flags", JSON, nullable=False),
    Column("normalizer_version", String(32), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("schema_version", String(8), nullable=False),
    Index("ix_api_events_available", "available_at", "event_id"),
    Index("ix_api_events_cluster", "cluster_id"),
)

event_instruments = Table(
    "api_event_instruments",
    metadata,
    Column("event_id", String(128), primary_key=True),
    Column("instrument_id", String(64), primary_key=True),
    Column("symbol", String(16), nullable=False),
    Column("confidence", Float, nullable=False),
    Column("available_at", UtcDateTime(), nullable=False),
    Index("ix_api_event_instruments_lookup", "instrument_id", "available_at"),
)

sentiment = Table(
    "api_sentiment",
    metadata,
    Column("normalized_event_id", String(128), primary_key=True),
    Column("scored_event_id", String(128), nullable=False),
    Column("model_id", String(64), nullable=False),
    Column("model_version", String(32), nullable=False),
    Column("calibration_version", String(32), nullable=False),
    Column("score_negative", Float, nullable=False),
    Column("score_neutral", Float, nullable=False),
    Column("score_positive", Float, nullable=False),
    Column("signed_sentiment", Float, nullable=False),
    Column("entropy", Float, nullable=False),
    Column("abstained", Boolean, nullable=False),
    Column("abstain_reason", String(32), nullable=True),
    Column("event_type", String(32), nullable=False),
    Column("materiality", Float, nullable=False),
    Column("novelty", Float, nullable=False),
    Column("rationale_terms", JSON, nullable=False),
    Column("available_at", UtcDateTime(), nullable=False),
    Index("ix_api_sentiment_available", "available_at"),
)

signals = Table(
    "api_signals",
    metadata,
    Column("signal_id", String(128), primary_key=True),
    Column("instrument_id", String(64), nullable=False),
    Column("symbol", String(16), nullable=False),
    Column("horizon", String(8), nullable=False),
    Column("as_of", UtcDateTime(), nullable=False),
    Column("available_at", UtcDateTime(), nullable=False),
    Column("score", Float, nullable=False),
    Column("score_normalized", Float, nullable=False),
    Column("stance", String(16), nullable=False),
    Column("abstained", Boolean, nullable=False),
    Column("payload", JSON, nullable=False),
    Index("ix_api_signals_lookup", "instrument_id", "horizon", "available_at"),
)

dead_letters = Table(
    "api_dead_letters",
    metadata,
    Column("dlq_id", String(128), primary_key=True),
    Column("service", String(64), nullable=False),
    Column("reason", String(32), nullable=False),
    Column("original_topic", String(255), nullable=False),
    Column("original_key", String(512), nullable=True),
    Column("error_detail", Text, nullable=False),
    Column("failed_at", UtcDateTime(), nullable=False),
    Column("trace_id", String(32), nullable=False),
)
