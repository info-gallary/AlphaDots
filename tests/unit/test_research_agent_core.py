"""The deterministic half of the agent: facts, citation verification, the bounded runner.

Nothing here talks to a network or a real model — ``complete_structured`` is exercised
with tiny hand-written adapters that implement :class:`~alphadots_llm.LlmAdapter`
directly, so the retry/cost/abort logic is tested independently of any specific provider.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from alphadots_llm import ChatMessage, ChatResult, LlmError, LlmErrorReason
from alphadots_research_agent.facts import (
    ALWAYS_MISSING,
    build_monitor_facts,
    build_research_facts,
    monitor_facts_to_prompt_json,
    research_facts_to_prompt_json,
)
from alphadots_research_agent.prompts import (
    DraftClaim,
    MonitorDraft,
    contains_recommendation_language,
    correction_message,
    monitor_messages,
    research_messages,
    verify_claims,
)
from alphadots_research_agent.runner import AgentAbort, LlmUsage, complete_structured

# ------------------------------------------------------------------------- facts

EVENT = {
    "event_id": "norm_a",
    "headline": "Beats estimates",
    "duplicate_of": None,
    "instruments": [{"symbol": "NWSC", "instrument_id": "SYN-0001", "confidence": 0.98}],
    "sentiment": {
        "signed_sentiment": 0.75,
        "event_type": "earnings",
        "materiality": 1.0,
        "abstained": False,
    },
}


def test_build_research_facts_splits_positive_and_negative_and_ranks_by_magnitude() -> None:
    events = {
        "data": [
            {
                **EVENT,
                "event_id": "e1",
                "headline": "Small win",
                "sentiment": {**EVENT["sentiment"], "signed_sentiment": 0.2},
            },
            {
                **EVENT,
                "event_id": "e2",
                "headline": "Big win",
                "sentiment": {**EVENT["sentiment"], "signed_sentiment": 0.9},
            },
            {
                **EVENT,
                "event_id": "e3",
                "headline": "Big loss",
                "sentiment": {**EVENT["sentiment"], "signed_sentiment": -0.8},
            },
        ]
    }
    facts = build_research_facts(
        ticker="NWSC",
        name="Northwind",
        as_of="2026-03-02T23:00:00Z",
        events_response=events,
        signal_response={"latest": None},
        extra_missing=[],
    )
    assert [e.event_id for e in facts.top_positive] == ["e2", "e1"]
    assert [e.event_id for e in facts.top_negative] == ["e3"]
    assert facts.event_count == 3
    assert facts.scored_count == 3
    assert facts.known_evidence == {"e1": "event", "e2": "event", "e3": "event"}


def test_build_research_facts_excludes_duplicates_and_abstained_from_scoring_but_counts_them() -> (
    None
):
    events = {
        "data": [
            {**EVENT, "event_id": "e1", "duplicate_of": "e0"},
            {**EVENT, "event_id": "e2", "sentiment": {**EVENT["sentiment"], "abstained": True}},
            {**EVENT, "event_id": "e3"},
        ]
    }
    facts = build_research_facts(
        ticker="NWSC",
        name="Northwind",
        as_of="2026-03-02T23:00:00Z",
        events_response=events,
        signal_response={"latest": None},
        extra_missing=[],
    )
    assert facts.event_count == 3
    assert facts.scored_count == 1
    assert facts.duplicate_count == 1
    # Duplicates and unscored events are still real, citable evidence of what happened.
    assert set(facts.known_evidence) == {"e1", "e2", "e3"}


def test_build_research_facts_caps_each_side_at_top_n() -> None:
    events = {
        "data": [
            {
                **EVENT,
                "event_id": f"e{i}",
                "sentiment": {**EVENT["sentiment"], "signed_sentiment": 0.1 * i},
            }
            for i in range(1, 6)
        ]
    }
    facts = build_research_facts(
        ticker="NWSC",
        name="Northwind",
        as_of="2026-03-02T23:00:00Z",
        events_response=events,
        signal_response={"latest": None},
        extra_missing=[],
    )
    assert len(facts.top_positive) == 3
    assert [e.event_id for e in facts.top_positive] == ["e5", "e4", "e3"]


def test_build_research_facts_always_discloses_missing_fundamentals() -> None:
    facts = build_research_facts(
        ticker="NWSC",
        name="Northwind",
        as_of="2026-03-02T23:00:00Z",
        events_response={"data": []},
        signal_response={"latest": None},
        extra_missing=[],
    )
    assert facts.missing_information == (ALWAYS_MISSING,)
    assert facts.event_count == 0
    assert facts.suggested_uncertainty == "high"


def test_build_research_facts_flags_unresolved_events() -> None:
    events = {"data": [{**EVENT, "event_id": "e1", "instruments": []}]}
    facts = build_research_facts(
        ticker="NWSC",
        name="Northwind",
        as_of="2026-03-02T23:00:00Z",
        events_response=events,
        signal_response={"latest": None},
        extra_missing=[],
    )
    assert any("could not be confidently linked" in note for note in facts.missing_information)


def test_build_research_facts_pulls_in_the_signal_as_citable_evidence() -> None:
    signal_response = {
        "latest": {
            "event_id": "sig_1",
            "stance": "positive",
            "score_normalized": 0.6,
            "as_of": "2026-03-02T23:00:00Z",
            "abstained": False,
        }
    }
    facts = build_research_facts(
        ticker="NWSC",
        name="Northwind",
        as_of="2026-03-02T23:00:00Z",
        events_response={"data": []},
        signal_response=signal_response,
        extra_missing=[],
    )
    assert facts.signal == {
        "stance": "positive",
        "score_normalized": 0.6,
        "as_of": "2026-03-02T23:00:00Z",
        "abstained": False,
    }
    assert facts.known_evidence["sig_1"] == "signal"


@pytest.mark.parametrize(
    ("scored_count", "signal", "expected"),
    [
        (0, None, "high"),
        (1, None, "high"),
        (2, {"abstained": True}, "high"),
        (2, {"abstained": False}, "medium"),
        (5, {"abstained": False}, "low"),
    ],
)
def test_suggested_uncertainty_rule(
    scored_count: int, signal: dict[str, object] | None, expected: str
) -> None:
    events = {"data": [{**EVENT, "event_id": f"e{i}"} for i in range(scored_count)]}
    facts = build_research_facts(
        ticker="NWSC",
        name="Northwind",
        as_of="x",
        events_response=events,
        signal_response={
            "latest": {
                "event_id": "s",
                "stance": "neutral",
                "score_normalized": 0.0,
                "as_of": "x",
                **signal,
            }
        }
        if signal
        else {"latest": None},
        extra_missing=[],
    )
    assert facts.suggested_uncertainty == expected


def test_research_facts_to_prompt_json_exposes_only_the_allowed_evidence() -> None:
    facts = build_research_facts(
        ticker="NWSC",
        name="Northwind Semiconductor",
        as_of="2026-03-02T23:00:00Z",
        events_response={"data": [{**EVENT, "event_id": "e1"}]},
        signal_response={"latest": None},
        extra_missing=[],
    )
    prompt_json = research_facts_to_prompt_json(facts)
    assert prompt_json["task"] == "research_brief"
    assert prompt_json["allowed_evidence_ids"] == ["e1"]
    assert prompt_json["top_positive"] == [
        {"event_id": "e1", "headline": "Beats estimates", "signed_sentiment": 0.75}
    ]
    assert "body" not in str(prompt_json), "body text must never reach the prompt (see facts.py)"


def test_build_monitor_facts_is_ok_when_nothing_crosses_a_threshold() -> None:
    overview = {
        "pipeline": {
            "dead_letters": 0,
            "scored": 10,
            "sentiment_abstained": 0,
            "events": 10,
            "unresolved": 0,
        }
    }
    facts = build_monitor_facts(overview=overview)
    assert facts.status == "ok"
    assert facts.findings[0].severity == "ok"


def test_build_monitor_facts_flags_each_threshold_independently() -> None:
    overview = {
        "pipeline": {
            "dead_letters": 1,
            "scored": 10,
            "sentiment_abstained": 6,
            "events": 10,
            "unresolved": 4,
        }
    }
    facts = build_monitor_facts(overview=overview)
    assert facts.status == "warning"
    metrics = {f.metric for f in facts.findings}
    assert metrics == {"dead_letters", "sentiment_abstention_rate", "unresolved_rate"}


def test_build_monitor_facts_dead_letter_severity_escalates() -> None:
    overview = {
        "pipeline": {
            "dead_letters": 20,
            "scored": 1,
            "sentiment_abstained": 0,
            "events": 1,
            "unresolved": 0,
        }
    }
    facts = build_monitor_facts(overview=overview)
    assert facts.status == "critical"


def test_build_monitor_facts_handles_a_zero_denominator_without_dividing_by_zero() -> None:
    overview = {
        "pipeline": {
            "dead_letters": 0,
            "scored": 0,
            "sentiment_abstained": 0,
            "events": 0,
            "unresolved": 0,
        }
    }
    facts = build_monitor_facts(overview=overview)
    assert facts.status == "ok"


def test_monitor_facts_to_prompt_json_gives_status_as_context_not_a_decision() -> None:
    """The model is shown ``status`` so its wording matches it, but never gets to set it —
    ``monitor_graph.py`` always takes ``status`` from these facts, never from the model's
    draft, which ``MonitorDraft`` does not even have a field for."""
    overview = {
        "pipeline": {
            "dead_letters": 5,
            "scored": 1,
            "sentiment_abstained": 0,
            "events": 1,
            "unresolved": 0,
        }
    }
    prompt_json = monitor_facts_to_prompt_json(build_monitor_facts(overview=overview))
    assert prompt_json["task"] == "monitor_summary"
    assert prompt_json["status"] == "warning"
    assert "status" not in MonitorDraft.model_fields
    assert all(
        {"severity", "title", "detail", "metric", "value"} == set(item)
        for item in prompt_json["findings"]
    )


# ------------------------------------------------------------------------ prompts


@pytest.mark.parametrize(
    "text",
    [
        "I would rate this a strong buy",
        "This gets a STRONG SELL from me",
        "Set a price target of $50",
        "overweight the position",
        "We recommend buying ahead of earnings",
        "buy this stock now before it moves",
    ],
)
def test_contains_recommendation_language_catches_direct_phrasing(text: str) -> None:
    assert contains_recommendation_language(text)


@pytest.mark.parametrize(
    "text",
    [
        "Revenue grew 12% year over year",
        "The company beat estimates and raised guidance",
        "Investors should read the filing for details",
    ],
)
def test_contains_recommendation_language_leaves_ordinary_prose_alone(text: str) -> None:
    assert not contains_recommendation_language(text)


def test_verify_claims_keeps_only_cited_non_recommendation_claims() -> None:
    known = {"e1": "event", "e2": "event"}
    candidates = [
        DraftClaim(claim="Revenue grew (signed sentiment +0.5).", evidence_id="e1"),
        DraftClaim(claim="I rate this a strong buy.", evidence_id="e2"),  # recommendation language
        DraftClaim(claim="Something happened.", evidence_id="e999"),  # not a real evidence ID
    ]
    kept, uncited, recommendation = verify_claims(candidates, known)
    assert [c.evidence_id for c in kept] == ["e1"]
    assert kept[0].evidence_kind == "event"
    assert uncited == 1
    assert recommendation == 1


def test_verify_claims_drops_a_claim_that_is_both_uncited_and_a_recommendation_once() -> None:
    candidates = [DraftClaim(claim="strong buy right now", evidence_id="not-real")]
    kept, uncited, recommendation = verify_claims(candidates, {})
    assert kept == []
    assert (uncited, recommendation) == (0, 1), (
        "the recommendation check runs first and short-circuits"
    )


def test_research_messages_tell_the_model_the_facts_block_is_data_not_instructions() -> None:
    messages = research_messages({"task": "research_brief", "allowed_evidence_ids": ["e1"]})
    assert messages[0].role == "system"
    assert "not an instruction" in messages[0].content or "inert data" in messages[0].content
    assert "```json" in messages[1].content
    assert '"allowed_evidence_ids": ["e1"]' in messages[1].content


def test_monitor_messages_tell_the_model_not_to_invent_a_severity() -> None:
    messages = monitor_messages({"task": "monitor_summary", "status": "ok"})
    assert "invent a severity" in messages[1].content


def test_correction_message_asks_for_exactly_one_json_object() -> None:
    message = correction_message("missing field 'headline'")
    assert message.role == "user"
    assert "missing field 'headline'" in message.content


# -------------------------------------------------------------------------- runner


class _ScriptedAdapter:
    """Replays a fixed sequence of responses/errors, one per call. Used to exercise the
    retry and abort paths deterministically, independent of any real adapter."""

    provider = "scripted"
    model = "scripted-v1"

    def __init__(self, script: list[ChatResult | LlmError]) -> None:
        self._script = list(script)
        self.calls: list[list[ChatMessage]] = []

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        json_mode: bool,
        max_tokens: int,
        timeout_seconds: float,
    ) -> ChatResult:
        self.calls.append(list(messages))
        outcome = self._script.pop(0)
        if isinstance(outcome, LlmError):
            raise outcome
        return outcome


def _result(content: str, *, cost: float | None = 0.0) -> ChatResult:
    return ChatResult(
        content=content, input_tokens=10, output_tokens=5, model="scripted-v1", cost_usd=cost
    )


class _Schema(BaseModel):
    value: str = Field(min_length=1)


BOUNDS = {
    "max_tokens": 100,
    "timeout_seconds": 5.0,
    "max_correction_attempts": 1,
    "max_cost_usd": 1.0,
    "abort_reason_invalid": "invalid",
    "abort_reason_unavailable": "unavailable",
    "abort_reason_cost": "cost",
}


async def test_complete_structured_returns_a_validated_result_on_the_first_try() -> None:
    adapter = _ScriptedAdapter([_result('{"value": "ok"}')])
    usage = LlmUsage()
    result = await complete_structured(
        adapter, [ChatMessage("user", "go")], _Schema, usage=usage, **BOUNDS
    )
    assert result.value == "ok"
    assert (usage.input_tokens, usage.output_tokens, usage.calls) == (10, 5, 1)
    assert usage.cost_usd == 0.0


async def test_complete_structured_retries_once_on_invalid_json_then_succeeds() -> None:
    adapter = _ScriptedAdapter([_result("not json at all"), _result('{"value": "fixed"}')])
    usage = LlmUsage()
    result = await complete_structured(
        adapter, [ChatMessage("user", "go")], _Schema, usage=usage, **BOUNDS
    )
    assert result.value == "fixed"
    assert usage.calls == 2
    assert len(adapter.calls[1]) == 3, (
        "the correction turn carries the bad reply plus the ask-again message"
    )


async def test_complete_structured_aborts_after_exhausting_correction_attempts() -> None:
    adapter = _ScriptedAdapter([_result("bad"), _result("still bad")])
    with pytest.raises(AgentAbort) as excinfo:
        await complete_structured(
            adapter, [ChatMessage("user", "go")], _Schema, usage=LlmUsage(), **BOUNDS
        )
    assert excinfo.value.reason == "invalid"


async def test_complete_structured_aborts_immediately_on_an_llm_error_no_retry_spent() -> None:
    adapter = _ScriptedAdapter([LlmError(LlmErrorReason.AUTH_ERROR, "bad key")])
    with pytest.raises(AgentAbort) as excinfo:
        await complete_structured(
            adapter, [ChatMessage("user", "go")], _Schema, usage=LlmUsage(), **BOUNDS
        )
    assert excinfo.value.reason == "unavailable"
    assert len(adapter.calls) == 1


async def test_complete_structured_rejects_a_schema_violation_not_just_bad_json() -> None:
    adapter = _ScriptedAdapter(
        [_result('{"value": ""}'), _result('{"value": ""}')]
    )  # empty string fails min_length
    with pytest.raises(AgentAbort):
        await complete_structured(
            adapter, [ChatMessage("user", "go")], _Schema, usage=LlmUsage(), **BOUNDS
        )


async def test_complete_structured_refuses_to_call_again_once_the_cost_bound_is_exceeded() -> None:
    bounds = {**BOUNDS, "max_cost_usd": 0.001, "max_correction_attempts": 3}
    adapter = _ScriptedAdapter([_result("bad", cost=1.0), _result('{"value": "ok"}')])
    usage = LlmUsage()
    with pytest.raises(AgentAbort) as excinfo:
        await complete_structured(
            adapter, [ChatMessage("user", "go")], _Schema, usage=usage, **bounds
        )
    assert excinfo.value.reason == "cost"
    assert len(adapter.calls) == 1, "the second call must never be placed once over budget"


async def test_llm_usage_cost_is_none_if_any_call_in_the_run_had_unknown_cost() -> None:
    adapter = _ScriptedAdapter([_result("bad", cost=None), _result('{"value": "ok"}', cost=0.5)])
    usage = LlmUsage()
    await complete_structured(
        adapter,
        [ChatMessage("user", "go")],
        _Schema,
        usage=usage,
        **{**BOUNDS, "max_correction_attempts": 1},
    )
    assert usage.cost_usd is None, (
        "a partial sum that hides an unknown component must not be shown as the total"
    )


def test_agent_abort_carries_its_reason_and_message() -> None:
    abort = AgentAbort("insufficient_evidence", "no events found")
    assert abort.reason == "insufficient_evidence"
    assert str(abort) == "no events found"
