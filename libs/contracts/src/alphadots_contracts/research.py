"""``reports.generated.v1`` — an agent-authored equity research brief.

A report narrates already-computed facts; it never supplies them. Every claim in
``bull_case`` and ``bear_case`` carries an ``evidence_id`` that must resolve to a stored
event or signal — the research agent's verifier drops any claim that does not, so an
invalid citation can never reach this message in the first place. ``research_only`` is
pinned ``True`` for the same reason it is on :class:`SignalGeneratedV1`: no report may be
read as a recommendation this schema itself did not reject.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from alphadots_contracts.base import BaseMessage, ContractModel, InstrumentRef, UtcDateTime

UncertaintyRating = Literal["low", "medium", "high"]


class ReportValidationStatus(StrEnum):
    """How much the report's narration has been checked. One rung today: the verifier
    confirms every citation resolves to real evidence. Nothing here validates that the
    narration itself is a good equity thesis — that is the reader's job."""

    UNVALIDATED_AGENT_OUTPUT = "unvalidated_agent_output"


class ReportAbstainReason(StrEnum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    AGENT_OUTPUT_INVALID = "agent_output_invalid"
    TOOL_BUDGET_EXCEEDED = "tool_budget_exceeded"
    COST_BOUND_EXCEEDED = "cost_bound_exceeded"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class Citation(ContractModel):
    """One claim, traced to one piece of stored evidence."""

    claim: str = Field(min_length=1, max_length=500)
    evidence_id: str = Field(min_length=1, max_length=128)
    evidence_kind: Literal["event", "signal"]


class ReportGeneratedV1(BaseMessage):
    instrument: InstrumentRef
    as_of: UtcDateTime = Field(description="Decision time the brief was written for.")
    job_id: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=64)
    model_version: str = Field(min_length=1, max_length=64)
    prompt_version: str = Field(min_length=1, max_length=32)
    validation_status: ReportValidationStatus
    research_only: Literal[True] = True
    abstained: bool
    abstain_reason: ReportAbstainReason | None = None
    business_snapshot: str = Field(max_length=1000)
    bull_case: tuple[Citation, ...] = ()
    bear_case: tuple[Citation, ...] = ()
    quant_summary: str = Field(max_length=500)
    missing_information: tuple[str, ...] = Field(
        default=(),
        description="What this brief could not speak to, stated plainly rather than guessed.",
    )
    uncertainty_rating: UncertaintyRating
    tool_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Null means the configured provider has no priced rate; show as 'not "
            "estimated', never as $0."
        ),
    )
    latency_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def _abstention_is_explained(self) -> Self:
        if self.abstained != (self.abstain_reason is not None):
            raise ValueError("abstain_reason must be set if and only if abstained is true")
        if self.abstained and (self.bull_case or self.bear_case):
            raise ValueError("an abstained report must carry no citations")
        return self

    @model_validator(mode="after")
    def _availability_follows_decision_time(self) -> Self:
        if self.available_at < self.as_of:
            raise ValueError("a report cannot be available before its decision time")
        return self
