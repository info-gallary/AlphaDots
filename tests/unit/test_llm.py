"""The LLM adapter layer: the mock's deterministic templates, Groq's wire behaviour, pricing."""

from __future__ import annotations

import json

import httpx
import pytest

from alphadots_llm import (
    ChatMessage,
    GroqAdapter,
    LlmError,
    LlmErrorReason,
    MockAdapter,
    TokenRate,
    estimate_cost_usd,
)

RESEARCH_FACTS = {
    "task": "research_brief",
    "ticker": "NWSC",
    "name": "Northwind Semiconductor Corp",
    "event_count": 2,
    "scored_count": 2,
    "top_positive": [
        {"event_id": "norm_a", "headline": "Beats estimates", "signed_sentiment": 0.75}
    ],
    "top_negative": [{"event_id": "norm_b", "headline": "Cuts guidance", "signed_sentiment": -0.5}],
    "signal": {
        "stance": "positive",
        "score_normalized": 0.6,
        "as_of": "2026-03-02T23:00:00Z",
        "abstained": False,
    },
    "suggested_uncertainty": "medium",
    "allowed_evidence_ids": ["norm_a", "norm_b"],
}
MONITOR_FACTS = {
    "task": "monitor_summary",
    "status": "warning",
    "findings": [
        {
            "severity": "warning",
            "title": "Records are being quarantined",
            "detail": "2 quarantined record(s)",
            "metric": "dead_letters",
            "value": 2.0,
        }
    ],
}


def _facts_message(facts: dict[str, object]) -> list[ChatMessage]:
    return [
        ChatMessage("system", "be a narrator"),
        ChatMessage("user", f"Facts:\n```json\n{json.dumps(facts)}\n```"),
    ]


# -------------------------------------------------------------------- MockAdapter


async def test_mock_renders_a_research_brief_deterministically_from_the_facts() -> None:
    adapter = MockAdapter()
    result = await adapter.complete(
        _facts_message(RESEARCH_FACTS), json_mode=True, max_tokens=900, timeout_seconds=10
    )
    draft = json.loads(result.content)
    assert draft["bull_case"] == [
        {
            "claim": "Beats estimates (signed sentiment +0.75).",
            "evidence_id": "norm_a",
            "evidence_kind": "event",
        }
    ]
    assert draft["bear_case"] == [
        {
            "claim": "Cuts guidance (signed sentiment -0.50).",
            "evidence_id": "norm_b",
            "evidence_kind": "event",
        }
    ]
    assert draft["uncertainty_rating"] == "medium"
    assert "positive at +0.60" in draft["quant_summary"]
    assert adapter.provider == "mock"
    assert result.model == "mock-template-v1"
    assert result.cost_usd == 0.0
    assert result.input_tokens > 0
    assert result.output_tokens > 0


async def test_mock_is_byte_identical_across_calls() -> None:
    adapter = MockAdapter()
    first = await adapter.complete(
        _facts_message(RESEARCH_FACTS), json_mode=True, max_tokens=900, timeout_seconds=10
    )
    second = await adapter.complete(
        _facts_message(RESEARCH_FACTS), json_mode=True, max_tokens=900, timeout_seconds=10
    )
    assert first.content == second.content


async def test_mock_renders_an_empty_bull_or_bear_case_when_there_is_nothing_to_cite() -> None:
    facts = {**RESEARCH_FACTS, "top_positive": [], "top_negative": []}
    adapter = MockAdapter()
    result = await adapter.complete(
        _facts_message(facts), json_mode=True, max_tokens=900, timeout_seconds=10
    )
    draft = json.loads(result.content)
    assert draft["bull_case"] == []
    assert draft["bear_case"] == []


async def test_mock_renders_monitor_narration_from_findings_and_never_sets_status() -> None:
    adapter = MockAdapter()
    result = await adapter.complete(
        _facts_message(MONITOR_FACTS), json_mode=True, max_tokens=900, timeout_seconds=10
    )
    draft = json.loads(result.content)
    assert set(draft) == {"headline", "summary"}, "the mock must never re-decide status"
    assert draft["headline"] == "Records are being quarantined."
    assert "2 quarantined record(s)" in draft["summary"]


async def test_mock_reports_pipeline_health_when_nothing_is_wrong() -> None:
    adapter = MockAdapter()
    healthy = {
        **MONITOR_FACTS,
        "status": "ok",
        "findings": [
            {"severity": "ok", "title": "Healthy", "detail": "", "metric": "status", "value": 0.0}
        ],
    }
    result = await adapter.complete(
        _facts_message(healthy), json_mode=True, max_tokens=900, timeout_seconds=10
    )
    draft = json.loads(result.content)
    assert draft["headline"] == "Pipeline looks healthy."


async def test_mock_raises_llm_error_with_no_json_fact_block() -> None:
    adapter = MockAdapter()
    with pytest.raises(LlmError) as excinfo:
        await adapter.complete(
            [ChatMessage("user", "no facts here")], json_mode=True, max_tokens=10, timeout_seconds=1
        )
    assert excinfo.value.reason is LlmErrorReason.INVALID_RESPONSE


async def test_mock_raises_llm_error_for_an_unknown_task() -> None:
    adapter = MockAdapter()
    facts = {"task": "something_else"}
    with pytest.raises(LlmError, match="no template"):
        await adapter.complete(
            _facts_message(facts), json_mode=True, max_tokens=10, timeout_seconds=1
        )


async def test_mock_uses_the_last_user_message_so_correction_turns_still_work() -> None:
    """``runner.py`` appends the model's own (invalid) prior reply plus a correction
    request as further user turns; the mock must still find the original facts block."""
    adapter = MockAdapter()
    messages = [
        *_facts_message(RESEARCH_FACTS),
        ChatMessage("user", "not json"),
    ]
    with pytest.raises(LlmError):
        await adapter.complete(messages, json_mode=True, max_tokens=10, timeout_seconds=1)


# -------------------------------------------------------------------- GroqAdapter


def _groq_client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_groq_success_parses_content_usage_and_computes_cost() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "llama-3.3-70b-versatile"
        assert body["temperature"] == 0
        assert body["max_tokens"] == 500
        assert body["response_format"] == {"type": "json_object"}
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "model": "llama-3.3-70b-versatile",
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 40},
            },
        )

    adapter = GroqAdapter(
        api_key="test-key",
        model="llama-3.3-70b-versatile",
        rate=TokenRate(0.1, 0.2),
        client=_groq_client(handler),
    )
    result = await adapter.complete(
        [ChatMessage("user", "hi")], json_mode=True, max_tokens=500, timeout_seconds=5
    )
    assert result.content == '{"ok": true}'
    assert (result.input_tokens, result.output_tokens) == (120, 40)
    assert result.cost_usd == pytest.approx((120 * 0.1 + 40 * 0.2) / 1_000_000)


async def test_groq_without_a_configured_rate_reports_cost_as_not_estimated() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            },
        )

    adapter = GroqAdapter(api_key="k", model="m", client=_groq_client(handler))
    result = await adapter.complete(
        [ChatMessage("user", "hi")], json_mode=False, max_tokens=10, timeout_seconds=5
    )
    assert result.cost_usd is None


async def test_groq_without_json_mode_omits_response_format() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "response_format" not in json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {}})

    adapter = GroqAdapter(api_key="k", model="m", client=_groq_client(handler))
    await adapter.complete(
        [ChatMessage("user", "hi")], json_mode=False, max_tokens=10, timeout_seconds=5
    )


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, LlmErrorReason.AUTH_ERROR),
        (429, LlmErrorReason.RATE_LIMITED),
        (500, LlmErrorReason.HTTP_ERROR),
        (400, LlmErrorReason.HTTP_ERROR),
    ],
)
async def test_groq_maps_http_status_codes_to_reasons(status: int, reason: LlmErrorReason) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="nope")

    adapter = GroqAdapter(api_key="k", model="m", client=_groq_client(handler))
    with pytest.raises(LlmError) as excinfo:
        await adapter.complete(
            [ChatMessage("user", "hi")], json_mode=False, max_tokens=10, timeout_seconds=5
        )
    assert excinfo.value.reason is reason


async def test_groq_a_malformed_200_body_is_an_invalid_response_not_a_crash() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    adapter = GroqAdapter(api_key="k", model="m", client=_groq_client(handler))
    with pytest.raises(LlmError) as excinfo:
        await adapter.complete(
            [ChatMessage("user", "hi")], json_mode=False, max_tokens=10, timeout_seconds=5
        )
    assert excinfo.value.reason is LlmErrorReason.INVALID_RESPONSE


async def test_groq_a_network_timeout_is_a_timeout_reason() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("took too long")

    adapter = GroqAdapter(api_key="k", model="m", client=_groq_client(handler))
    with pytest.raises(LlmError) as excinfo:
        await adapter.complete(
            [ChatMessage("user", "hi")], json_mode=False, max_tokens=10, timeout_seconds=0.01
        )
    assert excinfo.value.reason is LlmErrorReason.TIMEOUT


def test_groq_adapter_refuses_an_empty_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        GroqAdapter(api_key="", model="m")


# ---------------------------------------------------------------------- pricing


def test_estimate_cost_usd_is_none_without_a_rate() -> None:
    assert estimate_cost_usd(None, 1000, 1000) is None


def test_estimate_cost_usd_is_per_million_tokens() -> None:
    rate = TokenRate(input_per_million_usd=1.0, output_per_million_usd=2.0)
    assert estimate_cost_usd(rate, 1_000_000, 500_000) == pytest.approx(1.0 + 1.0)


def test_estimate_cost_usd_of_zero_tokens_is_zero_not_none() -> None:
    assert estimate_cost_usd(TokenRate(1.0, 1.0), 0, 0) == 0.0
