"""The research brief graph: retrieve → analyze → synthesize → verify → finalize.

``retrieve`` and ``analyze`` are plain, deterministic Python — they are the "Fundamentals
analyst" / "Sentiment analyst" / "Quant analyst" nodes from the specification's diagram,
except there is no fundamentals data to analyze yet (M1/M3), which the brief says plainly
rather than inventing. ``synthesize`` is the only node that calls a model, and only to
turn those facts into prose. ``verify`` is the citation and recommendation-language check
from ``prompts.py``. Any node may raise :class:`~alphadots_research_agent.runner.AgentAbort`,
which :func:`run_research` turns into an explicitly abstained, zero-citation report —
never a partial or best-guess one.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from alphadots_common.ids import derive_id, trace_id_for
from alphadots_common.logging import get_logger
from alphadots_common.metrics import AGENT_ABSTENTIONS, AGENT_RUN_SECONDS
from alphadots_common.timeutils import utc_now
from alphadots_contracts import (
    Citation,
    InstrumentRef,
    ReportAbstainReason,
    ReportGeneratedV1,
    ReportValidationStatus,
)
from alphadots_llm import LlmAdapter
from alphadots_research_agent.bounds import AgentBounds
from alphadots_research_agent.client import ApiReadClient, ToolBudget, ToolError, tracked
from alphadots_research_agent.facts import (
    ResearchFacts,
    build_research_facts,
    research_facts_to_prompt_json,
)
from alphadots_research_agent.prompts import (
    PROMPT_VERSION,
    DraftClaim,
    ResearchDraft,
    research_messages,
    verify_claims,
)
from alphadots_research_agent.runner import AgentAbort, LlmUsage, complete_structured

_log = get_logger(__name__)
GRAPH_NAME = "research_brief"
SENTIMENT_WINDOW = "14d"


class ResearchState(TypedDict, total=False):
    instrument: InstrumentRef
    name: str
    as_of: datetime
    events_response: dict[str, object]
    signal_response: dict[str, object]
    facts: ResearchFacts
    draft: ResearchDraft
    bull: list[Citation]
    bear: list[Citation]


def _build_graph(
    client: ApiReadClient,
    adapter: LlmAdapter,
    bounds: AgentBounds,
    budget: ToolBudget,
    usage: LlmUsage,
) -> Any:
    """Returns a compiled LangGraph graph. Left as ``Any``: its compiled-graph generics
    are deep enough that spelling them out would buy nothing here — the graph is built
    and invoked exactly once per run, immediately below, never reused or introspected."""

    async def retrieve(state: ResearchState) -> dict[str, object]:
        instrument, as_of = state["instrument"], state["as_of"]
        try:
            events_response = await tracked(
                "get_events",
                budget,
                lambda: client.get_events(symbol=instrument.symbol, as_of=as_of),
            )
        except ToolError as exc:
            raise AgentAbort(
                ReportAbstainReason.INSUFFICIENT_EVIDENCE, f"could not retrieve events: {exc}"
            ) from exc
        try:
            signal_response = await tracked(
                "get_signal",
                budget,
                lambda: client.get_signal(symbol=instrument.symbol, as_of=as_of),
            )
        except ToolError as exc:
            _log.warning("signal_retrieval_degraded", symbol=instrument.symbol, error=str(exc))
            signal_response = {"latest": None, "history": []}
        return {"events_response": events_response, "signal_response": signal_response}

    async def analyze(state: ResearchState) -> dict[str, object]:
        instrument = state["instrument"]
        facts = build_research_facts(
            ticker=instrument.symbol,
            name=state["name"],
            as_of=state["as_of"].isoformat(),
            events_response=state["events_response"],
            signal_response=state["signal_response"],
            extra_missing=[],
        )
        if facts.event_count == 0:
            raise AgentAbort(
                ReportAbstainReason.INSUFFICIENT_EVIDENCE,
                "no events were found for this issuer in range",
            )
        return {"facts": facts}

    async def synthesize(state: ResearchState) -> dict[str, object]:
        draft = await complete_structured(
            adapter,
            research_messages(research_facts_to_prompt_json(state["facts"])),
            ResearchDraft,
            max_tokens=bounds.max_tokens,
            timeout_seconds=bounds.timeout_seconds,
            max_correction_attempts=bounds.max_correction_attempts,
            max_cost_usd=bounds.max_cost_usd,
            usage=usage,
            abort_reason_invalid=ReportAbstainReason.AGENT_OUTPUT_INVALID,
            abort_reason_unavailable=ReportAbstainReason.PROVIDER_UNAVAILABLE,
            abort_reason_cost=ReportAbstainReason.COST_BOUND_EXCEEDED,
        )
        return {"draft": draft}

    async def verify(state: ResearchState) -> dict[str, object]:
        facts, draft = state["facts"], state["draft"]
        bull, bull_uncited, bull_flagged = verify_claims(draft.bull_case, facts.known_evidence)
        bear, bear_uncited, bear_flagged = verify_claims(draft.bear_case, facts.known_evidence)
        dropped = bull_uncited + bear_uncited + bull_flagged + bear_flagged
        if dropped:
            _log.warning(
                "claims_dropped_at_verification",
                ticker=facts.ticker,
                uncited=bull_uncited + bear_uncited,
                recommendation_language=bull_flagged + bear_flagged,
            )
        return {
            "bull": [
                Citation(claim=c.claim, evidence_id=c.evidence_id, evidence_kind=c.evidence_kind)
                for c in bull
            ],
            "bear": [
                Citation(claim=c.claim, evidence_id=c.evidence_id, evidence_kind=c.evidence_kind)
                for c in bear
            ],
        }

    graph = StateGraph(ResearchState)
    for name, fn in (
        ("retrieve", retrieve),
        ("analyze", analyze),
        ("synthesize", synthesize),
        ("verify", verify),
    ):
        graph.add_node(name, fn)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "analyze")
    graph.add_edge("analyze", "synthesize")
    graph.add_edge("synthesize", "verify")
    graph.add_edge("verify", END)
    return graph.compile()


def _abstained_report(
    *,
    instrument: InstrumentRef,
    as_of: datetime,
    job_id: str,
    reason: ReportAbstainReason,
    adapter: LlmAdapter,
    budget: ToolBudget,
    usage: LlmUsage,
    latency_ms: float,
    missing: tuple[str, ...],
) -> ReportGeneratedV1:
    event_id = derive_id("report", job_id)
    return ReportGeneratedV1(
        event_id=event_id,
        trace_id=trace_id_for(event_id),
        available_at=utc_now(),
        instrument=instrument,
        as_of=as_of,
        job_id=job_id,
        model_id=adapter.provider,
        model_version=adapter.model,
        prompt_version=PROMPT_VERSION,
        validation_status=ReportValidationStatus.UNVALIDATED_AGENT_OUTPUT,
        abstained=True,
        abstain_reason=reason,
        business_snapshot="Insufficient evidence.",
        bull_case=(),
        bear_case=(),
        quant_summary="Insufficient evidence.",
        missing_information=missing,
        uncertainty_rating="high",
        tool_calls=budget.used,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
        latency_ms=latency_ms,
    )


async def run_research(
    *,
    instrument: InstrumentRef,
    name: str,
    as_of: datetime,
    job_id: str,
    client: ApiReadClient,
    adapter: LlmAdapter,
    bounds: AgentBounds,
) -> ReportGeneratedV1:
    budget = ToolBudget(max_calls=bounds.max_tool_calls)
    usage = LlmUsage()
    started = time.perf_counter()
    graph = _build_graph(client, adapter, bounds, budget, usage)
    try:
        final_state: ResearchState = await graph.ainvoke(
            {"instrument": instrument, "name": name, "as_of": as_of}
        )
    except AgentAbort as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        AGENT_RUN_SECONDS.labels(graph=GRAPH_NAME, outcome="abstained").observe(latency_ms / 1000)
        AGENT_ABSTENTIONS.labels(graph=GRAPH_NAME, reason=str(exc.reason)).inc()
        _log.info(
            "research_abstained",
            ticker=instrument.symbol,
            job_id=job_id,
            reason=exc.reason,
            detail=str(exc),
        )
        return _abstained_report(
            instrument=instrument,
            as_of=as_of,
            job_id=job_id,
            reason=ReportAbstainReason(exc.reason),
            adapter=adapter,
            budget=budget,
            usage=usage,
            latency_ms=latency_ms,
            missing=(str(exc),),
        )

    latency_ms = (time.perf_counter() - started) * 1000
    facts, draft = final_state["facts"], final_state["draft"]
    event_id = derive_id("report", job_id)
    report = ReportGeneratedV1(
        event_id=event_id,
        trace_id=trace_id_for(event_id),
        available_at=utc_now(),
        instrument=instrument,
        as_of=as_of,
        job_id=job_id,
        model_id=adapter.provider,
        model_version=adapter.model,
        prompt_version=PROMPT_VERSION,
        validation_status=ReportValidationStatus.UNVALIDATED_AGENT_OUTPUT,
        abstained=False,
        business_snapshot=draft.business_snapshot,
        bull_case=tuple(final_state["bull"]),
        bear_case=tuple(final_state["bear"]),
        quant_summary=draft.quant_summary,
        missing_information=facts.missing_information,
        uncertainty_rating=facts.suggested_uncertainty,
        tool_calls=budget.used,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
        latency_ms=latency_ms,
    )
    AGENT_RUN_SECONDS.labels(graph=GRAPH_NAME, outcome="completed").observe(latency_ms / 1000)
    _log.info(
        "research_completed",
        ticker=instrument.symbol,
        job_id=job_id,
        tool_calls=budget.used,
        cost_usd=usage.cost_usd,
        bull_claims=len(report.bull_case),
        bear_claims=len(report.bear_case),
    )
    return report


__all__ = ["GRAPH_NAME", "DraftClaim", "ResearchState", "run_research"]
