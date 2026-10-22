"""``signals.generated.v1`` — deterministic, research-only signal output."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from alphadots_contracts.base import (
    BaseMessage,
    ContractModel,
    InstrumentRef,
    UnitInterval,
    UtcDateTime,
)

Horizon = Literal["1d", "5d"]


class Stance(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class ValidationStatus(StrEnum):
    """How much empirical support a signal has. Consumers must surface this."""

    UNVALIDATED_HEURISTIC = "unvalidated_heuristic"
    OUT_OF_SAMPLE_EVALUATED = "out_of_sample_evaluated"


class SignalAbstainReason(StrEnum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NO_USABLE_EVENTS = "no_usable_events"


class RiskFlag(StrEnum):
    LOW_COVERAGE = "low_coverage"
    AMBIGUOUS_ISSUER = "ambiguous_issuer"
    SINGLE_SOURCE = "single_source"


class SignalParams(ContractModel):
    tau_seconds: float = Field(gt=0)
    lookback_seconds: float = Field(gt=0)
    tau_source: Literal["config_prior_unestimated", "estimated_on_training_folds"]
    stance_threshold: UnitInterval
    min_effective_weight: float = Field(ge=0)


class SignalCoverage(ContractModel):
    events_considered: int = Field(ge=0)
    events_used: int = Field(ge=0)
    events_abstained: int = Field(ge=0)
    duplicates_excluded: int = Field(ge=0)
    effective_weight: float = Field(ge=0)


class SignalContribution(ContractModel):
    """One evidence item behind a signal, so every number is traceable to a source."""

    scored_event_id: str
    normalized_event_id: str
    available_at: UtcDateTime
    age_seconds: float = Field(ge=0)
    weight: float = Field(ge=0)
    signed_sentiment: float = Field(ge=-1.0, le=1.0)
    contribution: float


class SignalGeneratedV1(BaseMessage):
    instrument: InstrumentRef
    as_of: UtcDateTime = Field(
        description="Decision time. Every input satisfies available_at <= as_of."
    )
    horizon: Horizon
    signal_kind: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=64)
    model_version: str = Field(min_length=1, max_length=32)
    validation_status: ValidationStatus
    research_only: Literal[True] = True
    score: float = Field(description="Raw decayed sentiment sum S(i,t); unbounded.")
    score_normalized: float = Field(ge=-1.0, le=1.0, description="Weight-normalised S(i,t).")
    stance: Stance
    abstained: bool
    abstain_reason: SignalAbstainReason | None = None
    params: SignalParams
    coverage: SignalCoverage
    contributions: tuple[SignalContribution, ...] = ()
    contributions_truncated: bool = False
    risk_flags: tuple[RiskFlag, ...] = ()
    triggering_event_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _temporal_and_abstention_invariants(self) -> Self:
        if self.available_at < self.as_of:
            raise ValueError("a signal cannot be available before its decision time")
        late = [c.scored_event_id for c in self.contributions if c.available_at > self.as_of]
        if late:
            raise ValueError(f"contributions available after as_of (look-ahead): {late}")
        if self.abstained != (self.abstain_reason is not None):
            raise ValueError("abstain_reason must be set if and only if abstained is true")
        if self.abstained and self.stance is not Stance.NEUTRAL:
            raise ValueError("an abstained signal must have a neutral stance")
        return self
