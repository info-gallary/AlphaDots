"""``reports.generated.v1`` contract invariants, mirroring ``test_messages.py``'s style."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from alphadots_contracts import ReportGeneratedV1
from tests.conftest import T0


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "event_id": "report_test",
        "trace_id": "0" * 32,
        "available_at": (T0 + timedelta(seconds=1)).isoformat(),
        "instrument": {"symbol": "NWSC", "instrument_id": "SYN-0001", "confidence": 1.0},
        "as_of": T0.isoformat(),
        "job_id": "job-1",
        "model_id": "mock",
        "model_version": "mock-template-v1",
        "prompt_version": "research-agent-prompts-v1",
        "validation_status": "unvalidated_agent_output",
        "abstained": False,
        "business_snapshot": "Northwind had one tracked item.",
        "bull_case": [
            {"claim": "Beats estimates.", "evidence_id": "norm_a", "evidence_kind": "event"}
        ],
        "bear_case": [],
        "quant_summary": "No signal yet.",
        "missing_information": ["Fundamentals are not ingested yet."],
        "uncertainty_rating": "medium",
        "tool_calls": 2,
        "input_tokens": 400,
        "output_tokens": 100,
        "cost_usd": 0.0001,
        "latency_ms": 250.0,
    }
    base.update(overrides)
    return base


def test_a_well_formed_report_round_trips() -> None:
    report = ReportGeneratedV1.model_validate(_payload())
    assert ReportGeneratedV1.model_validate_json(report.model_dump_json()) == report
    assert report.research_only is True


def test_research_only_cannot_be_turned_off() -> None:
    with pytest.raises(ValidationError):
        ReportGeneratedV1.model_validate(_payload(research_only=False))


def test_abstain_reason_must_accompany_abstained() -> None:
    with pytest.raises(ValidationError, match="abstain_reason"):
        ReportGeneratedV1.model_validate(_payload(abstained=True))
    with pytest.raises(ValidationError, match="abstain_reason"):
        ReportGeneratedV1.model_validate(_payload(abstain_reason="insufficient_evidence"))


def test_a_valid_abstained_report_has_no_citations_at_all() -> None:
    report = ReportGeneratedV1.model_validate(
        _payload(abstained=True, abstain_reason="insufficient_evidence", bull_case=[], bear_case=[])
    )
    assert report.abstained is True
    assert (report.bull_case, report.bear_case) == ((), ())


def test_an_abstained_report_is_rejected_if_it_still_carries_a_citation() -> None:
    with pytest.raises(ValidationError, match="no citations"):
        ReportGeneratedV1.model_validate(
            _payload(abstained=True, abstain_reason="insufficient_evidence", bear_case=[])
        )


def test_availability_cannot_precede_the_decision_time() -> None:
    with pytest.raises(ValidationError, match="before its decision time"):
        ReportGeneratedV1.model_validate(
            _payload(available_at=(T0 - timedelta(seconds=1)).isoformat())
        )


def test_available_at_exactly_equal_to_as_of_is_allowed() -> None:
    report = ReportGeneratedV1.model_validate(_payload(available_at=T0.isoformat()))
    assert report.available_at == report.as_of


@pytest.mark.parametrize("field", ["tool_calls", "input_tokens", "output_tokens", "latency_ms"])
def test_run_accounting_fields_cannot_be_negative(field: str) -> None:
    with pytest.raises(ValidationError):
        ReportGeneratedV1.model_validate(_payload(**{field: -1}))


def test_cost_usd_can_be_null_meaning_not_estimated() -> None:
    report = ReportGeneratedV1.model_validate(_payload(cost_usd=None))
    assert report.cost_usd is None


def test_cost_usd_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        ReportGeneratedV1.model_validate(_payload(cost_usd=-0.01))


@pytest.mark.parametrize("rating", ["low", "medium", "high"])
def test_uncertainty_rating_accepts_every_documented_level(rating: str) -> None:
    assert (
        ReportGeneratedV1.model_validate(_payload(uncertainty_rating=rating)).uncertainty_rating
        == rating
    )


def test_uncertainty_rating_rejects_anything_else() -> None:
    with pytest.raises(ValidationError):
        ReportGeneratedV1.model_validate(_payload(uncertainty_rating="certain"))


def test_citation_requires_a_known_evidence_kind() -> None:
    with pytest.raises(ValidationError):
        ReportGeneratedV1.model_validate(
            _payload(bull_case=[{"claim": "x", "evidence_id": "e1", "evidence_kind": "rumor"}])
        )


def test_citation_rejects_an_empty_claim_or_evidence_id() -> None:
    with pytest.raises(ValidationError):
        ReportGeneratedV1.model_validate(
            _payload(bull_case=[{"claim": "", "evidence_id": "e1", "evidence_kind": "event"}])
        )
    with pytest.raises(ValidationError):
        ReportGeneratedV1.model_validate(
            _payload(bull_case=[{"claim": "x", "evidence_id": "", "evidence_kind": "event"}])
        )


def test_the_message_is_immutable() -> None:
    report = ReportGeneratedV1.model_validate(_payload())
    with pytest.raises(ValidationError):
        report.business_snapshot = "edited"  # type: ignore[misc]
