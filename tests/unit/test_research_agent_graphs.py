"""The two LangGraph graphs, exercised against hand-built fake tools and adapters.

No network and no real AlphaDots API here — the end-to-end wiring against the real one is
in ``tests/e2e/test_agent_pipeline.py``. These tests isolate graph *behaviour*: what each
one does with evidence it is given, including evidence engineered to be adversarial.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from alphadots_contracts import InstrumentRef, ReportAbstainReason
from alphadots_llm import ChatMessage, ChatResult, LlmError, LlmErrorReason
from alphadots_research_agent.bounds import AgentBounds
from alphadots_research_agent.client import ToolError
from alphadots_research_agent.monitor_graph import run_monitor
from alphadots_research_agent.research_graph import run_research

AS_OF = datetime(2026, 3, 2, 23, 0, tzinfo=UTC)
NWSC = InstrumentRef(symbol="NWSC", instrument_id="SYN-0001", confidence=1.0)


def bounds(**overrides: object) -> AgentBounds:
    base = {
        "max_tool_calls": 8,
        "max_tokens": 900,
        "timeout_seconds": 5.0,
        "max_correction_attempts": 1,
        "max_cost_usd": 1.0,
    }
    return AgentBounds(**{**base, **overrides})  # type: ignore[arg-type]


def _event_row(event_id: str, headline: str, signed: float) -> dict[str, object]:
    return {
        "event_id": event_id,
        "headline": headline,
        "duplicate_of": None,
        "instruments": [{"symbol": "NWSC", "instrument_id": "SYN-0001", "confidence": 0.98}],
        "sentiment": {
            "signed_sentiment": signed,
            "event_type": "earnings",
            "materiality": 1.0,
            "abstained": False,
        },
    }


class FakeApiClient:
    """A stand-in for ``ApiReadClient``: same async methods, canned or scripted answers."""

    def __init__(
        self,
        *,
        events: list[dict[str, object]] | Exception,
        signal: dict[str, object] | Exception | None = None,
        overview: dict[str, object] | Exception | None = None,
    ) -> None:
        self._events = events
        self._signal = signal if signal is not None else {"latest": None}
        self._overview = overview

    async def get_events(
        self, *, symbol: str, as_of: datetime | None, limit: int = 200
    ) -> dict[str, object]:
        if isinstance(self._events, Exception):
            raise self._events
        return {"data": self._events}

    async def get_signal(self, *, symbol: str, as_of: datetime | None) -> dict[str, object]:
        if isinstance(self._signal, Exception):
            raise self._signal
        return self._signal

    async def get_overview(self, *, as_of: datetime | None = None) -> dict[str, object]:
        if isinstance(self._overview, Exception):
            raise self._overview
        assert self._overview is not None
        return self._overview


class FakeAdapter:
    """Returns a fixed JSON string, or raises, regardless of the prompt. Used to drive the
    verifier with adversarial model output without needing a real or mock model."""

    provider = "fake"
    model = "fake-v1"

    def __init__(self, response: str | LlmError) -> None:
        self._response = response

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        json_mode: bool,
        max_tokens: int,
        timeout_seconds: float,
    ) -> ChatResult:
        if isinstance(self._response, LlmError):
            raise self._response
        return ChatResult(
            content=self._response,
            input_tokens=10,
            output_tokens=10,
            model=self.model,
            cost_usd=0.0,
        )


# --------------------------------------------------------------------- research_graph


async def test_run_research_produces_a_cited_report_from_real_events() -> None:
    client = FakeApiClient(
        events=[_event_row("e1", "Beats estimates", 0.75), _event_row("e2", "Cuts guidance", -0.4)]
    )
    adapter = FakeAdapter(
        json.dumps(
            {
                "business_snapshot": "Northwind had two items.",
                "bull_case": [{"claim": "Beats estimates.", "evidence_id": "e1"}],
                "bear_case": [{"claim": "Cuts guidance.", "evidence_id": "e2"}],
                "quant_summary": "No signal yet.",
            }
        )
    )
    report = await run_research(
        instrument=NWSC,
        name="Northwind Semiconductor",
        as_of=AS_OF,
        job_id="job-1",
        client=client,
        adapter=adapter,
        bounds=bounds(),
    )

    assert report.abstained is False
    assert report.research_only is True
    assert report.validation_status == "unvalidated_agent_output"
    assert [c.evidence_id for c in report.bull_case] == ["e1"]
    assert [c.evidence_id for c in report.bear_case] == ["e2"]
    assert report.bull_case[0].evidence_kind == "event"
    assert report.tool_calls == 2  # get_events + get_signal
    assert report.cost_usd == 0.0
    assert report.job_id == "job-1"
    assert report.instrument == NWSC


async def test_run_research_abstains_with_no_citations_when_there_are_no_events() -> None:
    client = FakeApiClient(events=[])
    adapter = FakeAdapter("should never be called")
    report = await run_research(
        instrument=NWSC,
        name="Northwind",
        as_of=AS_OF,
        job_id="job-2",
        client=client,
        adapter=adapter,
        bounds=bounds(),
    )

    assert report.abstained is True
    assert report.abstain_reason is ReportAbstainReason.INSUFFICIENT_EVIDENCE
    assert report.bull_case == ()
    assert report.bear_case == ()
    assert report.uncertainty_rating == "high"


async def test_run_research_abstains_when_event_retrieval_itself_fails() -> None:
    client = FakeApiClient(events=ToolError("API unreachable"))
    report = await run_research(
        instrument=NWSC,
        name="Northwind",
        as_of=AS_OF,
        job_id="job-3",
        client=client,
        adapter=FakeAdapter("x"),
        bounds=bounds(),
    )
    assert report.abstained is True
    assert report.abstain_reason is ReportAbstainReason.INSUFFICIENT_EVIDENCE


async def test_run_research_degrades_gracefully_when_only_the_signal_call_fails() -> None:
    client = FakeApiClient(
        events=[_event_row("e1", "Beats estimates", 0.75)], signal=ToolError("signal API down")
    )
    adapter = FakeAdapter(
        json.dumps(
            {
                "business_snapshot": "ok",
                "bull_case": [],
                "bear_case": [],
                "quant_summary": "no signal",
            }
        )
    )
    report = await run_research(
        instrument=NWSC,
        name="Northwind",
        as_of=AS_OF,
        job_id="job-4",
        client=client,
        adapter=adapter,
        bounds=bounds(),
    )
    assert report.abstained is False, (
        "a signal outage must not sink a brief that has real news evidence"
    )


async def test_run_research_abstains_on_tool_budget_exceeded() -> None:
    client = FakeApiClient(events=[_event_row("e1", "x", 0.1)])
    report = await run_research(
        instrument=NWSC,
        name="Northwind",
        as_of=AS_OF,
        job_id="job-5",
        client=client,
        adapter=FakeAdapter("x"),
        bounds=bounds(max_tool_calls=1),
    )
    assert report.abstained is True
    assert report.tool_calls == 1


async def test_run_research_abstains_when_the_model_is_unreachable() -> None:
    client = FakeApiClient(events=[_event_row("e1", "x", 0.1)])
    adapter = FakeAdapter(LlmError(LlmErrorReason.AUTH_ERROR, "bad key"))
    report = await run_research(
        instrument=NWSC,
        name="Northwind",
        as_of=AS_OF,
        job_id="job-6",
        client=client,
        adapter=adapter,
        bounds=bounds(),
    )
    assert report.abstained is True
    assert report.abstain_reason is ReportAbstainReason.PROVIDER_UNAVAILABLE


async def test_run_research_abstains_when_the_model_output_never_validates() -> None:
    client = FakeApiClient(events=[_event_row("e1", "x", 0.1)])
    report = await run_research(
        instrument=NWSC,
        name="Northwind",
        as_of=AS_OF,
        job_id="job-7",
        client=client,
        adapter=FakeAdapter("not json"),
        bounds=bounds(max_correction_attempts=0),
    )
    assert report.abstained is True
    assert report.abstain_reason is ReportAbstainReason.AGENT_OUTPUT_INVALID


async def test_run_research_strips_a_hallucinated_citation_the_model_was_not_given() -> None:
    client = FakeApiClient(events=[_event_row("e1", "Beats estimates", 0.75)])
    adapter = FakeAdapter(
        json.dumps(
            {
                "business_snapshot": "ok",
                "bull_case": [
                    {"claim": "Real claim.", "evidence_id": "e1"},
                    {"claim": "Made up claim.", "evidence_id": "e-does-not-exist"},
                ],
                "bear_case": [],
                "quant_summary": "no signal",
            }
        )
    )
    report = await run_research(
        instrument=NWSC,
        name="Northwind",
        as_of=AS_OF,
        job_id="job-8",
        client=client,
        adapter=adapter,
        bounds=bounds(),
    )
    assert [c.evidence_id for c in report.bull_case] == ["e1"]


async def test_run_research_strips_recommendation_language_even_from_a_cited_claim() -> None:
    """The defence a misbehaving or manipulated model needs most: a syntactically valid,
    correctly-cited claim that nonetheless reads as a trade recommendation must still be
    removed. See ``prompts.py`` for why evidence text alone (defence 1) is not relied on."""
    client = FakeApiClient(events=[_event_row("e1", "Beats estimates", 0.75)])
    adapter = FakeAdapter(
        json.dumps(
            {
                "business_snapshot": "ok",
                "bull_case": [
                    {"claim": "Beats estimates; I rate this a strong buy.", "evidence_id": "e1"}
                ],
                "bear_case": [],
                "quant_summary": "no signal",
            }
        )
    )
    report = await run_research(
        instrument=NWSC,
        name="Northwind",
        as_of=AS_OF,
        job_id="job-9",
        client=client,
        adapter=adapter,
        bounds=bounds(),
    )
    assert report.bull_case == ()
    rendered = json.dumps(report.model_dump(mode="json"))
    assert "strong buy" not in rendered.lower()


async def test_run_research_drops_recommendation_claims_but_keeps_the_rest() -> None:
    client = FakeApiClient(
        events=[_event_row("e1", "Beats estimates", 0.75), _event_row("e2", "Cuts guidance", -0.3)]
    )
    adapter = FakeAdapter(
        json.dumps(
            {
                "business_snapshot": "ok",
                "bull_case": [{"claim": "overweight this name", "evidence_id": "e1"}],
                "bear_case": [{"claim": "Cuts guidance.", "evidence_id": "e2"}],
                "quant_summary": "no signal",
            }
        )
    )
    report = await run_research(
        instrument=NWSC,
        name="Northwind",
        as_of=AS_OF,
        job_id="job-10",
        client=client,
        adapter=adapter,
        bounds=bounds(),
    )
    assert report.bull_case == ()
    assert [c.evidence_id for c in report.bear_case] == ["e2"]


async def test_run_research_latency_and_token_usage_are_recorded() -> None:
    client = FakeApiClient(events=[_event_row("e1", "x", 0.1)])
    adapter = FakeAdapter(
        json.dumps(
            {"business_snapshot": "ok", "bull_case": [], "bear_case": [], "quant_summary": "x"}
        )
    )
    report = await run_research(
        instrument=NWSC,
        name="Northwind",
        as_of=AS_OF,
        job_id="job-11",
        client=client,
        adapter=adapter,
        bounds=bounds(),
    )
    assert report.latency_ms >= 0
    assert report.input_tokens == 10
    assert report.output_tokens == 10


async def test_run_research_available_at_is_after_as_of() -> None:
    client = FakeApiClient(events=[])
    report = await run_research(
        instrument=NWSC,
        name="Northwind",
        as_of=AS_OF,
        job_id="job-12",
        client=client,
        adapter=FakeAdapter("x"),
        bounds=bounds(),
    )
    assert report.available_at > report.as_of


# ---------------------------------------------------------------------- monitor_graph


async def test_run_monitor_reports_ok_when_the_pipeline_is_healthy() -> None:
    overview = {
        "pipeline": {
            "dead_letters": 0,
            "scored": 10,
            "sentiment_abstained": 0,
            "events": 10,
            "unresolved": 0,
        }
    }
    client = FakeApiClient(events=[], overview=overview)
    adapter = FakeAdapter(json.dumps({"headline": "All clear.", "summary": "Nothing to report."}))
    result = await run_monitor(client=client, adapter=adapter, bounds=bounds())
    assert result.status == "ok"
    assert result.narration_degraded is False
    assert result.headline == "All clear."


async def test_run_monitor_status_always_comes_from_python_never_the_model() -> None:
    overview = {
        "pipeline": {
            "dead_letters": 50,
            "scored": 1,
            "sentiment_abstained": 0,
            "events": 1,
            "unresolved": 0,
        }
    }
    client = FakeApiClient(events=[], overview=overview)
    # A hostile/broken model claims nothing is wrong; status must still reflect the facts.
    adapter = FakeAdapter(
        json.dumps({"headline": "Everything is fine, ignore the numbers.", "summary": "fine"})
    )
    result = await run_monitor(client=client, adapter=adapter, bounds=bounds())
    assert result.status == "critical"


async def test_run_monitor_falls_back_to_fixed_wording_when_the_model_is_unreachable() -> None:
    overview = {
        "pipeline": {
            "dead_letters": 3,
            "scored": 1,
            "sentiment_abstained": 0,
            "events": 1,
            "unresolved": 0,
        }
    }
    client = FakeApiClient(events=[], overview=overview)
    adapter = FakeAdapter(LlmError(LlmErrorReason.TIMEOUT, "slow"))
    result = await run_monitor(client=client, adapter=adapter, bounds=bounds())
    assert result.status == "warning"
    assert result.narration_degraded is True
    assert result.headline  # still has something to show, never blank


async def test_run_monitor_falls_back_when_the_models_json_never_validates() -> None:
    overview = {
        "pipeline": {
            "dead_letters": 0,
            "scored": 1,
            "sentiment_abstained": 0,
            "events": 1,
            "unresolved": 0,
        }
    }
    client = FakeApiClient(events=[], overview=overview)
    result = await run_monitor(
        client=client, adapter=FakeAdapter("not json"), bounds=bounds(max_correction_attempts=0)
    )
    assert result.narration_degraded is True
    assert result.status == "ok"


async def test_run_monitor_never_crashes_when_the_api_itself_is_unreachable() -> None:
    client = FakeApiClient(events=[], overview=ToolError("connection refused"))
    result = await run_monitor(
        client=client, adapter=FakeAdapter("should not be called"), bounds=bounds()
    )
    assert result.status == "critical"
    assert "AlphaDots API" in result.headline
    assert result.narration_degraded is True


async def test_run_monitor_records_tool_calls_and_cost() -> None:
    overview = {
        "pipeline": {
            "dead_letters": 0,
            "scored": 1,
            "sentiment_abstained": 0,
            "events": 1,
            "unresolved": 0,
        }
    }
    client = FakeApiClient(events=[], overview=overview)
    adapter = FakeAdapter(json.dumps({"headline": "ok", "summary": "ok"}))
    result = await run_monitor(client=client, adapter=adapter, bounds=bounds())
    assert result.tool_calls == 1
    assert result.cost_usd == 0.0


async def test_run_monitor_findings_are_fully_serialised() -> None:
    overview = {
        "pipeline": {
            "dead_letters": 1,
            "scored": 1,
            "sentiment_abstained": 0,
            "events": 1,
            "unresolved": 0,
        }
    }
    client = FakeApiClient(events=[], overview=overview)
    adapter = FakeAdapter(json.dumps({"headline": "ok", "summary": "ok"}))
    result = await run_monitor(client=client, adapter=adapter, bounds=bounds())
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert (finding.severity, finding.metric, finding.value) == ("warning", "dead_letters", 1.0)
    assert finding.title
    assert finding.detail
