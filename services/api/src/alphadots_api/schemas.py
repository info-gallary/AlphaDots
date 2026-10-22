"""Response models for the REST API.

Every response carries ``meta`` with the schema version, the ``as_of`` that was applied,
the feed mode and a research-only marker, so a screenshot of any payload is self-describing.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from alphadots_contracts import SCHEMA_VERSION, ReportGeneratedV1, SignalGeneratedV1

FeedMode = Literal["simulated", "delayed", "live"]
DISCLAIMER = "Research output. Not investment advice and not an instruction to trade."


class Meta(BaseModel):
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    as_of: datetime | None = Field(
        description="Point-in-time bound applied to this response; null means latest."
    )
    feed_mode: FeedMode
    research_only: Literal[True] = True
    disclaimer: str = DISCLAIMER


class ErrorBody(BaseModel):
    code: str
    message: str
    trace_id: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class Page(BaseModel):
    limit: int
    next_cursor: str | None


class InstrumentOut(BaseModel):
    instrument_id: str
    symbol: str
    name: str
    sector: str
    all_symbols: list[str]


class InstrumentRefOut(BaseModel):
    symbol: str
    instrument_id: str
    confidence: float


class PolarityOut(BaseModel):
    negative: float
    neutral: float
    positive: float


class SentimentOut(BaseModel):
    scored_event_id: str
    model_id: str
    model_version: str
    calibration_version: str
    scores: PolarityOut
    signed_sentiment: float
    entropy: float
    abstained: bool
    abstain_reason: str | None
    event_type: str
    materiality: float
    novelty: float
    rationale_terms: list[str]
    available_at: datetime

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> SentimentOut | None:
        if row.get("s_scored_event_id") is None:
            return None
        return cls(
            scored_event_id=row["s_scored_event_id"],
            model_id=row["s_model_id"],
            model_version=row["s_model_version"],
            calibration_version=row["s_calibration_version"],
            scores=PolarityOut(
                negative=row["s_score_negative"],
                neutral=row["s_score_neutral"],
                positive=row["s_score_positive"],
            ),
            signed_sentiment=row["s_signed_sentiment"],
            entropy=row["s_entropy"],
            abstained=row["s_abstained"],
            abstain_reason=row["s_abstain_reason"],
            event_type=row["s_event_type"],
            materiality=row["s_materiality"],
            novelty=row["s_novelty"],
            rationale_terms=list(row["s_rationale_terms"]),
            available_at=row["s_available_at"],
        )


class EventOut(BaseModel):
    event_id: str
    raw_event_id: str
    source: str
    source_event_id: str
    source_url: str
    source_license: str
    payload_ref: str
    content_hash: str
    cluster_id: str
    duplicate_of: str | None
    headline: str
    body: str | None
    language: str
    published_at: datetime
    first_seen_at: datetime
    ingested_at: datetime
    available_at: datetime
    source_latency_seconds: float = Field(description="first_seen_at minus published_at.")
    instruments: list[InstrumentRefOut]
    resolution_flags: list[str]
    normalizer_version: str
    trace_id: str
    sentiment: SentimentOut | None = Field(
        description="Null until a score is available as of the requested time."
    )

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> EventOut:
        return cls(
            event_id=row["event_id"],
            raw_event_id=row["raw_event_id"],
            source=row["source"],
            source_event_id=row["source_event_id"],
            source_url=row["source_url"],
            source_license=row["source_license"],
            payload_ref=row["payload_ref"],
            content_hash=row["content_hash"],
            cluster_id=row["cluster_id"],
            duplicate_of=row["duplicate_of"],
            headline=row["headline"],
            body=row["body"],
            language=row["language"],
            published_at=row["published_at"],
            first_seen_at=row["first_seen_at"],
            ingested_at=row["ingested_at"],
            available_at=row["available_at"],
            source_latency_seconds=(row["first_seen_at"] - row["published_at"]).total_seconds(),
            instruments=[InstrumentRefOut(**ref) for ref in row["instruments"]],
            resolution_flags=list(row["resolution_flags"]),
            normalizer_version=row["normalizer_version"],
            trace_id=row["trace_id"],
            sentiment=SentimentOut.from_row(row),
        )


class EventsResponse(BaseModel):
    data: list[EventOut]
    page: Page
    meta: Meta


class ClusterMemberOut(BaseModel):
    event_id: str
    source: str
    source_event_id: str
    first_seen_at: datetime
    available_at: datetime
    is_canonical: bool


class EventDetailResponse(BaseModel):
    data: EventOut
    cluster: list[ClusterMemberOut]
    meta: Meta


class InstrumentsResponse(BaseModel):
    data: list[InstrumentOut]
    reference_version: str
    reference_license: str
    meta: Meta


class SentimentPointOut(BaseModel):
    event_id: str
    headline: str
    source: str
    published_at: datetime
    available_at: datetime
    signed_sentiment: float
    entropy: float
    abstained: bool
    abstain_reason: str | None
    is_duplicate: bool
    event_type: str
    materiality: float
    relevance: float


class SentimentSummaryOut(BaseModel):
    events: int
    used: int = Field(description="Scored, not abstained, not a duplicate.")
    abstained: int
    duplicates: int
    positive: int
    negative: int
    neutral: int
    mean_signed_sentiment: float | None = Field(
        description="Unweighted mean over `used`; null when nothing is usable."
    )
    mean_entropy: float | None


class SentimentResponse(BaseModel):
    instrument: InstrumentOut
    window: str
    window_start: datetime
    summary: SentimentSummaryOut
    points: list[SentimentPointOut]
    points_truncated: bool
    meta: Meta


class SignalPointOut(BaseModel):
    signal_id: str
    as_of: datetime
    available_at: datetime
    score: float
    score_normalized: float
    stance: str
    abstained: bool


class SignalsResponse(BaseModel):
    instrument: InstrumentOut
    horizon: str
    latest: SignalGeneratedV1 | None
    history: list[SignalPointOut]
    meta: Meta


class FeedOut(BaseModel):
    mode: FeedMode
    earliest_available_at: datetime | None
    latest_available_at: datetime | None


class PipelineCountsOut(BaseModel):
    events: int
    duplicates: int
    unresolved: int
    scored: int
    sentiment_abstained: int
    signals: int
    signals_abstained: int
    dead_letters: int


class InstrumentOverviewOut(BaseModel):
    instrument: InstrumentOut
    event_count: int
    latest_signal: SignalPointOut | None
    score_history: list[SignalPointOut]


class OverviewResponse(BaseModel):
    feed: FeedOut
    pipeline: PipelineCountsOut
    instruments: list[InstrumentOverviewOut]
    meta: Meta


class DeadLetterOut(BaseModel):
    dlq_id: str
    service: str
    reason: str
    original_topic: str
    original_key: str | None
    error_detail: str
    failed_at: datetime
    trace_id: str


class DeadLettersResponse(BaseModel):
    data: list[DeadLetterOut]
    meta: Meta


# -------------------------------------------------------------------- research agent

JobStatus = Literal["pending", "running", "succeeded", "failed"]


class ResearchJobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=16)
    as_of: datetime | None = Field(
        default=None, description="Decision time for the brief. Omit to mean 'now'."
    )


class ResearchJobOut(BaseModel):
    job_id: str
    symbol: str
    status: JobStatus
    as_of: datetime
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    report: ReportGeneratedV1 | None = Field(
        description=(
            "Set once status is 'succeeded'. An abstained report is still a 'succeeded' "
            "job — abstention is a completed, honest answer, not a failure."
        )
    )
    error: str | None = Field(description="Set only when status is 'failed': the run itself broke.")


class ResearchJobResponse(BaseModel):
    data: ResearchJobOut
    meta: Meta


class ResearchBriefResponse(BaseModel):
    data: ReportGeneratedV1 | None = Field(
        description="Null when no brief has been generated for this issuer yet."
    )
    meta: Meta


# -------------------------------------------------------------- agentic monitoring

Severity = Literal["ok", "warning", "critical"]


class MonitorFindingOut(BaseModel):
    severity: Severity
    title: str
    detail: str
    metric: str
    value: float


class MonitorOut(BaseModel):
    generated_at: datetime
    status: Severity
    headline: str
    summary: str
    findings: list[MonitorFindingOut]
    model_id: str
    model_version: str
    narration_degraded: bool = Field(
        description=(
            "True only when the prose fell back to a fixed template; status/findings "
            "are decided in Python either way, never by the model."
        ),
    )
    tool_calls: int
    cost_usd: float | None
    latency_ms: float


class MonitorResponse(BaseModel):
    data: MonitorOut
    cached: bool = Field(description="True if this was served from the agent's short-lived cache.")
    meta: Meta
