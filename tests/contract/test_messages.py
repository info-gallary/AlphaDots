"""Contract rules every message must obey, independent of any service."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from alphadots_contracts import (
    RESERVED_TOPICS,
    TOPIC_MODELS,
    NormalizedEventV1,
    PolarityScores,
    RawNewsV1,
    SentimentScoredV1,
    SignalGeneratedV1,
    Topic,
    model_for,
)
from tests.conftest import T0, make_normalized, make_raw, make_scored


def _payload(message: RawNewsV1) -> dict[str, object]:
    return json.loads(message.model_dump_json())


def test_every_topic_has_a_model_or_is_explicitly_reserved() -> None:
    assert set(TOPIC_MODELS) | RESERVED_TOPICS == set(Topic)
    assert not set(TOPIC_MODELS) & RESERVED_TOPICS


def test_topic_names_carry_their_major_version() -> None:
    assert all(topic.value.endswith(".v1") for topic in Topic)
    assert model_for("news.raw.v1") is RawNewsV1
    with pytest.raises(KeyError):
        model_for(Topic.MARKET_BARS.value)


@pytest.mark.parametrize("builder", [make_raw, make_normalized, make_scored])
def test_json_round_trip_is_lossless(builder: object) -> None:
    message = builder()  # type: ignore[operator]
    assert type(message).model_validate_json(message.model_dump_json()) == message


def test_naive_timestamps_are_rejected() -> None:
    payload = _payload(make_raw())
    payload["published_at"] = "2026-03-02T14:00:00"
    with pytest.raises(ValidationError, match="timezone-aware"):
        RawNewsV1.model_validate(payload)


def test_offsets_are_normalised_to_utc() -> None:
    payload = _payload(make_raw())
    payload["published_at"] = "2026-03-02T09:00:00-05:00"
    parsed = RawNewsV1.model_validate(payload)
    assert parsed.published_at == datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
    assert parsed.published_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("available_at", "2026-03-02T13:59:59Z"),  # before ingested_at
        ("ingested_at", "2026-03-02T13:59:00Z"),  # before first_seen_at
    ],
)
def test_availability_must_not_precede_observation(field: str, value: str) -> None:
    payload = _payload(make_raw())
    payload[field] = value
    with pytest.raises(
        ValidationError, match=re.escape("first_seen_at <= ingested_at <= available_at")
    ):
        RawNewsV1.model_validate(payload)


def test_published_at_may_be_skewed_relative_to_observation() -> None:
    # A source clock that runs ahead is a fact about the source, not an invalid message.
    message = make_raw(published_at=T0 + timedelta(seconds=30))
    assert message.published_at > message.first_seen_at


def test_unknown_fields_are_ignored_for_forward_compatibility() -> None:
    payload = _payload(make_raw())
    payload["schema_version"] = "1.7"
    payload["field_added_in_a_later_minor_version"] = {"anything": 1}
    parsed = RawNewsV1.model_validate(payload)
    assert parsed.schema_version == "1.7"
    assert not hasattr(parsed, "field_added_in_a_later_minor_version")


def test_a_different_major_version_is_rejected() -> None:
    payload = _payload(make_raw())
    payload["schema_version"] = "2.0"
    with pytest.raises(ValidationError):
        RawNewsV1.model_validate(payload)


def test_source_url_must_be_http() -> None:
    payload = _payload(make_raw())
    payload["source_url"] = "javascript:alert(1)"
    with pytest.raises(ValidationError):
        RawNewsV1.model_validate(payload)


def test_messages_are_immutable() -> None:
    message = make_raw()
    with pytest.raises(ValidationError):
        message.headline = "edited"  # type: ignore[misc]


def test_polarity_scores_must_sum_to_one() -> None:
    with pytest.raises(ValidationError, match=re.escape("sum to 1.0")):
        PolarityScores(negative=0.5, neutral=0.5, positive=0.5)


def test_abstention_requires_a_reason_and_vice_versa() -> None:
    payload = json.loads(make_scored().model_dump_json())
    payload["abstained"] = True
    with pytest.raises(ValidationError, match="abstain_reason"):
        SentimentScoredV1.model_validate(payload)


def _signal_payload() -> dict[str, object]:
    as_of = T0.isoformat()
    return {
        "event_id": "sig_test",
        "trace_id": "0" * 32,
        "available_at": (T0 + timedelta(seconds=1)).isoformat(),
        "instrument": {"symbol": "NWSC", "instrument_id": "SYN-0001", "confidence": 1.0},
        "as_of": as_of,
        "horizon": "1d",
        "signal_kind": "test",
        "model_id": "m",
        "model_version": "v",
        "validation_status": "unvalidated_heuristic",
        "score": 0.5,
        "score_normalized": 0.5,
        "stance": "positive",
        "abstained": False,
        "params": {
            "tau_seconds": 86400,
            "lookback_seconds": 432000,
            "tau_source": "config_prior_unestimated",
            "stance_threshold": 0.15,
            "min_effective_weight": 0.2,
        },
        "coverage": {
            "events_considered": 1,
            "events_used": 1,
            "events_abstained": 0,
            "duplicates_excluded": 0,
            "effective_weight": 1.0,
        },
        "contributions": [
            {
                "scored_event_id": "sent_a",
                "normalized_event_id": "norm_a",
                "available_at": as_of,
                "age_seconds": 0,
                "weight": 1.0,
                "signed_sentiment": 0.5,
                "contribution": 0.5,
            }
        ],
        "triggering_event_id": "sent_a",
    }


def test_valid_signal_is_accepted_and_always_research_only() -> None:
    signal = SignalGeneratedV1.model_validate(_signal_payload())
    assert signal.research_only is True
    payload = _signal_payload()
    payload["research_only"] = False
    with pytest.raises(ValidationError):
        SignalGeneratedV1.model_validate(payload)


def test_signal_rejects_a_contribution_from_after_its_decision_time() -> None:
    payload = _signal_payload()
    payload["contributions"][0]["available_at"] = (  # type: ignore[index]
        T0 + timedelta(seconds=1)
    ).isoformat()
    with pytest.raises(ValidationError, match="look-ahead"):
        SignalGeneratedV1.model_validate(payload)


def test_signal_cannot_be_available_before_it_was_decided() -> None:
    payload = _signal_payload()
    payload["available_at"] = (T0 - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValidationError, match="before its decision time"):
        SignalGeneratedV1.model_validate(payload)


def test_abstained_signal_must_be_neutral() -> None:
    payload = _signal_payload()
    payload.update(abstained=True, abstain_reason="insufficient_evidence")
    with pytest.raises(ValidationError, match="neutral stance"):
        SignalGeneratedV1.model_validate(payload)


def test_normalized_event_keeps_raw_lineage() -> None:
    event = make_normalized()
    assert isinstance(event, NormalizedEventV1)
    assert event.raw_event_id
    assert event.payload_ref.startswith("fixture://")
