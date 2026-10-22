"""Source-derived events: raw news, normalised events and sentiment scores."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from alphadots_contracts.base import ContractModel, EventEnvelope, UnitInterval


class RawNewsV1(EventEnvelope):
    """``news.raw.v1`` — a news item exactly as received from a source adapter.

    ``instruments`` is empty at this stage: ``provider_symbols`` are untrusted hints, and
    entity resolution belongs to the preprocessor.
    """

    headline: str = Field(min_length=1, max_length=1000)
    body: str | None = Field(default=None, max_length=20000)
    language: str | None = Field(default=None, max_length=16)
    provider_symbols: tuple[str, ...] = ()


class ResolutionFlag(StrEnum):
    UNRESOLVED = "unresolved"
    AMBIGUOUS_ISSUER = "ambiguous_issuer"
    PROVIDER_SYMBOL_UNKNOWN = "provider_symbol_unknown"
    MULTI_ISSUER = "multi_issuer"


class NormalizedEventV1(EventEnvelope):
    """``events.normalized.v1`` — cleaned text, dedup cluster and resolved instruments."""

    raw_event_id: str = Field(min_length=1, max_length=128)
    headline: str = Field(min_length=1, max_length=1000)
    body: str | None = Field(default=None, max_length=20000)
    language: str = Field(min_length=2, max_length=16)
    cluster_id: str = Field(min_length=1, max_length=64)
    duplicate_of: str | None = Field(
        default=None,
        description="event_id of the canonical event in this dedup cluster, if this is a copy.",
    )
    resolution_flags: tuple[ResolutionFlag, ...] = ()
    normalizer_version: str = Field(min_length=1, max_length=32)


class EventType(StrEnum):
    EARNINGS = "earnings"
    GUIDANCE = "guidance"
    M_AND_A = "m_and_a"
    REGULATORY = "regulatory"
    PRODUCT = "product"
    MANAGEMENT = "management"
    MACRO = "macro"
    OTHER = "other"


class AbstainReason(StrEnum):
    NO_LEXICON_MATCH = "no_lexicon_match"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    HIGH_ENTROPY = "high_entropy"


class PolarityScores(ContractModel):
    """A distribution over polarity classes.

    Only a calibrated model may call these probabilities. ``calibration_version`` on the
    parent message says whether that is the case.
    """

    negative: UnitInterval
    neutral: UnitInterval
    positive: UnitInterval

    @model_validator(mode="after")
    def _sums_to_one(self) -> Self:
        total = self.negative + self.neutral + self.positive
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"polarity scores must sum to 1.0, got {total:.6f}")
        return self


class SentimentScoredV1(EventEnvelope):
    """``sentiment.scored.v1`` — polarity for one normalised event.

    This is Task A (financial NLP). It is not a return prediction.
    """

    normalized_event_id: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=64)
    model_version: str = Field(min_length=1, max_length=32)
    calibration_version: str = Field(min_length=1, max_length=32)
    scores: PolarityScores
    signed_sentiment: float = Field(ge=-1.0, le=1.0, description="positive minus negative")
    entropy: UnitInterval = Field(description="Shannon entropy of `scores`, normalised by ln 3.")
    abstained: bool
    abstain_reason: AbstainReason | None = None
    event_type: EventType
    materiality: UnitInterval
    novelty: UnitInterval
    rationale_terms: tuple[str, ...] = Field(
        default=(), description="Terms a transparent model matched; empty for opaque models."
    )

    @model_validator(mode="after")
    def _abstention_is_explained(self) -> Self:
        if self.abstained != (self.abstain_reason is not None):
            raise ValueError("abstain_reason must be set if and only if abstained is true")
        return self
