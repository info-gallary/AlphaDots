"""Deterministic math over retrieved evidence — the part of the agent that is not the LLM.

Every number a report or a monitor summary states comes from here, computed in plain
Python from the AlphaDots API's own read endpoints. The LLM only narrates these facts
(see ``prompts.py``); it never produces a count, a score, a severity or an evidence ID of
its own. That is what keeps a brief auditable and keeps the quantitative/operational path
deterministic even though the prose wrapped around it is not.

A deliberate v1 scope limit, not an oversight: :class:`EvidenceEvent` carries a
``headline`` but never a news item's full body text. Body text is untrusted, unbounded
and the one place a fixture has ever carried an injected instruction (see
``news/seed.jsonl``, "Ignore all previous instructions..."); keeping it out of every
prompt entirely is a stronger defence than relying on the model to ignore it, and the
dashboard still links every citation back to the full event, body included, for a human
to read. Reconsider this once a richer defence (e.g. a separate untrusted-content
classifier) is worth building.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ------------------------------------------------------------- research brief facts

TOP_N = 3
LOW_UNCERTAINTY_MIN_SCORED = 5

ALWAYS_MISSING = (
    "Fundamentals and filings are not ingested yet (milestones M1 and M3); this brief "
    "covers news sentiment and an unvalidated heuristic signal only, not valuation or "
    "filing-backed facts."
)


@dataclass(frozen=True, slots=True)
class EvidenceEvent:
    event_id: str
    headline: str
    signed_sentiment: float


@dataclass(frozen=True, slots=True)
class ResearchFacts:
    """Everything the LLM is allowed to talk about, already computed and already cited."""

    ticker: str
    name: str
    as_of: str
    event_count: int
    scored_count: int
    duplicate_count: int
    top_positive: tuple[EvidenceEvent, ...]
    top_negative: tuple[EvidenceEvent, ...]
    signal: dict[str, Any] | None
    """``None`` when no signal has been computed yet for this instrument."""
    suggested_uncertainty: Literal["low", "medium", "high"]
    missing_information: tuple[str, ...]
    known_evidence: dict[str, Literal["event", "signal"]] = field(default_factory=dict)
    """Every evidence ID the model may cite. The verifier checks against this — not
    against the smaller ``top_positive``/``top_negative`` lists, which are only a hint."""


def _suggest_uncertainty(
    scored_count: int, signal: dict[str, Any] | None
) -> Literal["low", "medium", "high"]:
    if scored_count == 0 or signal is None or signal.get("abstained"):
        return "high"
    if scored_count >= LOW_UNCERTAINTY_MIN_SCORED:
        return "low"
    return "medium"


def build_research_facts(
    *,
    ticker: str,
    name: str,
    as_of: str,
    events_response: dict[str, Any],
    signal_response: dict[str, Any],
    extra_missing: list[str],
) -> ResearchFacts:
    rows: list[dict[str, Any]] = events_response.get("data", [])
    scored: list[EvidenceEvent] = []
    known: dict[str, Literal["event", "signal"]] = {}
    duplicate_count = 0
    unresolved_seen = False

    for row in rows:
        known[row["event_id"]] = "event"
        if not row.get("instruments"):
            unresolved_seen = True
        if row.get("duplicate_of"):
            duplicate_count += 1
            continue
        sentiment = row.get("sentiment")
        if sentiment is None or sentiment.get("abstained"):
            continue
        scored.append(
            EvidenceEvent(
                event_id=row["event_id"],
                headline=row["headline"],
                signed_sentiment=sentiment["signed_sentiment"],
            )
        )

    positives = sorted(
        (e for e in scored if e.signed_sentiment > 0), key=lambda e: -e.signed_sentiment
    )
    negatives = sorted(
        (e for e in scored if e.signed_sentiment < 0), key=lambda e: e.signed_sentiment
    )

    latest_signal = signal_response.get("latest")
    signal_fact: dict[str, Any] | None = None
    if latest_signal is not None:
        signal_fact = {
            "stance": latest_signal["stance"],
            "score_normalized": latest_signal["score_normalized"],
            "as_of": latest_signal["as_of"],
            "abstained": latest_signal["abstained"],
        }
        known[latest_signal["event_id"]] = "signal"

    missing = [ALWAYS_MISSING, *extra_missing]
    if unresolved_seen:
        missing.append(
            "Some retrieved items could not be confidently linked to an issuer and were "
            "left out of scoring."
        )

    return ResearchFacts(
        ticker=ticker,
        name=name,
        as_of=as_of,
        event_count=len(rows),
        scored_count=len(scored),
        duplicate_count=duplicate_count,
        top_positive=tuple(positives[:TOP_N]),
        top_negative=tuple(negatives[:TOP_N]),
        signal=signal_fact,
        suggested_uncertainty=_suggest_uncertainty(len(scored), signal_fact),
        missing_information=tuple(missing),
        known_evidence=known,
    )


def research_facts_to_prompt_json(facts: ResearchFacts) -> dict[str, Any]:
    """The exact structure embedded in the LLM prompt's ```json``` fact block."""

    def event_dict(event: EvidenceEvent) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "headline": event.headline,
            "signed_sentiment": event.signed_sentiment,
        }

    return {
        "task": "research_brief",
        "ticker": facts.ticker,
        "name": facts.name,
        "event_count": facts.event_count,
        "scored_count": facts.scored_count,
        "top_positive": [event_dict(e) for e in facts.top_positive],
        "top_negative": [event_dict(e) for e in facts.top_negative],
        "signal": facts.signal,
        "suggested_uncertainty": facts.suggested_uncertainty,
        "allowed_evidence_ids": sorted(facts.known_evidence),
    }


# ------------------------------------------------------------------- monitor facts

# Priors, not learned thresholds. Revisit once real operating experience accumulates.
DLQ_WARNING_AT = 1
DLQ_CRITICAL_AT = 20
ABSTENTION_WARNING_RATE = 0.5
UNRESOLVED_WARNING_RATE = 0.3

Severity = Literal["ok", "warning", "critical"]


@dataclass(frozen=True, slots=True)
class Finding:
    severity: Severity
    title: str
    detail: str
    metric: str
    value: float


@dataclass(frozen=True, slots=True)
class MonitorFacts:
    status: Severity
    findings: tuple[Finding, ...]


def build_monitor_facts(*, overview: dict[str, Any]) -> MonitorFacts:
    pipeline = overview["pipeline"]
    findings: list[Finding] = []

    dlq = pipeline["dead_letters"]
    if dlq >= DLQ_CRITICAL_AT:
        findings.append(
            Finding(
                "critical",
                "Dead-letter queue is large",
                f"{dlq} quarantined records",
                "dead_letters",
                dlq,
            )
        )
    elif dlq >= DLQ_WARNING_AT:
        findings.append(
            Finding(
                "warning",
                "Records are being quarantined",
                f"{dlq} quarantined record(s)",
                "dead_letters",
                dlq,
            )
        )

    scored = pipeline["scored"]
    if scored > 0:
        rate = pipeline["sentiment_abstained"] / scored
        if rate >= ABSTENTION_WARNING_RATE:
            findings.append(
                Finding(
                    "warning",
                    "High sentiment abstention",
                    f"{rate:.0%} of scored events abstained",
                    "sentiment_abstention_rate",
                    rate,
                )
            )

    events = pipeline["events"]
    if events > 0:
        rate = pipeline["unresolved"] / events
        if rate >= UNRESOLVED_WARNING_RATE:
            findings.append(
                Finding(
                    "warning",
                    "Many events have no issuer link",
                    f"{rate:.0%} of events are unresolved",
                    "unresolved_rate",
                    rate,
                )
            )

    status: Severity = "ok"
    if any(item.severity == "critical" for item in findings):
        status = "critical"
    elif any(item.severity == "warning" for item in findings):
        status = "warning"
    if not findings:
        findings.append(
            Finding("ok", "Healthy", "No monitored thresholds were crossed.", "status", 0.0)
        )
    return MonitorFacts(status=status, findings=tuple(findings))


def monitor_facts_to_prompt_json(facts: MonitorFacts) -> dict[str, Any]:
    return {
        "task": "monitor_summary",
        "status": facts.status,
        "findings": [
            {
                "severity": item.severity,
                "title": item.title,
                "detail": item.detail,
                "metric": item.metric,
                "value": item.value,
            }
            for item in facts.findings
        ],
    }
