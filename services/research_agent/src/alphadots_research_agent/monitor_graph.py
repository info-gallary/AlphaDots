"""The pipeline-monitoring graph: collect → analyze → narrate → finalize.

Unlike the research brief, this graph never abstains into silence — a dashboard health
panel that can disappear is worse than one with plain wording. ``collect`` and
``analyze`` are deterministic and decide the only thing that actually matters
(``status`` and the list of crossed thresholds); ``narrate`` only asks a model for a
human-readable headline and summary of what Python already decided, and falls back to a
fixed, still-accurate sentence if the model is unavailable or its reply will not
validate. The model is never the one deciding whether anything is wrong.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from alphadots_common.logging import get_logger
from alphadots_common.metrics import AGENT_RUN_SECONDS
from alphadots_llm import LlmAdapter
from alphadots_research_agent.bounds import AgentBounds
from alphadots_research_agent.client import ApiReadClient, ToolBudget, ToolError, tracked
from alphadots_research_agent.facts import (
    Finding,
    MonitorFacts,
    Severity,
    build_monitor_facts,
    monitor_facts_to_prompt_json,
)
from alphadots_research_agent.prompts import PROMPT_VERSION, MonitorDraft, monitor_messages
from alphadots_research_agent.runner import AgentAbort, LlmUsage, complete_structured

_log = get_logger(__name__)
GRAPH_NAME = "monitor_summary"


class MonitorFindingOut(BaseModel):
    severity: Severity
    title: str
    detail: str
    metric: str
    value: float


class MonitorResult(BaseModel):
    generated_at: datetime
    status: Severity
    headline: str
    summary: str
    findings: tuple[MonitorFindingOut, ...]
    model_id: str
    model_version: str
    prompt_version: str
    narration_degraded: bool
    """True when the model could not be reached or its reply did not validate, so
    ``headline``/``summary`` fell back to a fixed template. ``status``/``findings`` are
    unaffected either way — they never come from the model."""
    tool_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    latency_ms: float


def _fallback_narration(facts: MonitorFacts) -> tuple[str, str]:
    """The same safety-net wording the deterministic mock adapter renders, kept as an
    independent copy on purpose: this path must keep working even if that module does
    not, since it is what stands in when a live model call fails."""
    worst = next((f for f in facts.findings if f.severity in ("warning", "critical")), None)
    if worst is None:
        return (
            "Pipeline looks healthy.",
            "No abstention, backlog or quarantine thresholds were crossed.",
        )
    headline = f"{worst.title}."
    summary = " ".join(
        f"{item.title}: {item.detail}" for item in facts.findings if item.severity != "ok"
    )
    return headline, summary


class _MonitorState(TypedDict, total=False):
    overview: dict[str, object]
    facts: MonitorFacts
    headline: str
    summary: str
    degraded: bool


def _build_graph(
    client: ApiReadClient,
    adapter: LlmAdapter,
    bounds: AgentBounds,
    budget: ToolBudget,
    usage: LlmUsage,
) -> Any:
    """Returns a compiled LangGraph graph; see the matching note in ``research_graph.py``."""

    async def collect(_state: _MonitorState) -> dict[str, object]:
        overview = await tracked("get_overview", budget, client.get_overview)
        return {"overview": overview}

    async def analyze(state: _MonitorState) -> dict[str, object]:
        return {"facts": build_monitor_facts(overview=state["overview"])}

    async def narrate(state: _MonitorState) -> dict[str, object]:
        facts = state["facts"]
        try:
            draft = await complete_structured(
                adapter,
                monitor_messages(monitor_facts_to_prompt_json(facts)),
                MonitorDraft,
                max_tokens=bounds.max_tokens,
                timeout_seconds=bounds.timeout_seconds,
                max_correction_attempts=bounds.max_correction_attempts,
                max_cost_usd=bounds.max_cost_usd,
                usage=usage,
                abort_reason_invalid="agent_output_invalid",
                abort_reason_unavailable="provider_unavailable",
                abort_reason_cost="cost_bound_exceeded",
            )
            return {"headline": draft.headline, "summary": draft.summary, "degraded": False}
        except AgentAbort as exc:
            _log.warning("monitor_narration_degraded", reason=exc.reason, detail=str(exc))
            headline, summary = _fallback_narration(facts)
            return {"headline": headline, "summary": summary, "degraded": True}

    graph: Any = StateGraph(_MonitorState)
    # mypy cannot solve StateGraph's NodeInputT from this call for reasons that do not
    # reproduce in research_graph.py's structurally identical pattern; `graph: Any` above
    # sidesteps it rather than suppressing real checking of the three node functions
    # themselves, which keep their own precise signatures and are checked normally.
    graph.add_node("collect", collect)
    graph.add_node("analyze", analyze)
    graph.add_node("narrate", narrate)
    graph.add_edge(START, "collect")
    graph.add_edge("collect", "analyze")
    graph.add_edge("analyze", "narrate")
    graph.add_edge("narrate", END)
    return graph.compile()


async def run_monitor(
    *, client: ApiReadClient, adapter: LlmAdapter, bounds: AgentBounds
) -> MonitorResult:
    budget = ToolBudget(max_calls=bounds.max_tool_calls)
    usage = LlmUsage()
    started = time.perf_counter()
    try:
        graph = _build_graph(client, adapter, bounds, budget, usage)
        final_state: _MonitorState = await graph.ainvoke({})
        facts = final_state["facts"]
        headline, summary, degraded = (
            final_state["headline"],
            final_state["summary"],
            final_state["degraded"],
        )
    except ToolError as exc:
        # Nothing to narrate without the overview; say so plainly rather than guessing.
        _log.error("monitor_collection_failed", error=str(exc))
        facts = MonitorFacts(
            status="critical",
            findings=(
                Finding(
                    "critical", "Monitoring data unavailable", str(exc)[:300], "api_reachable", 0.0
                ),
            ),
        )
        headline, summary, degraded = "Could not reach the AlphaDots API.", str(exc)[:300], True

    latency_ms = (time.perf_counter() - started) * 1000
    AGENT_RUN_SECONDS.labels(
        graph=GRAPH_NAME, outcome="degraded" if degraded else "completed"
    ).observe(latency_ms / 1000)
    return MonitorResult(
        generated_at=datetime.now(UTC),
        status=facts.status,
        headline=headline,
        summary=summary,
        findings=tuple(MonitorFindingOut(**asdict(item)) for item in facts.findings),
        model_id=adapter.provider,
        model_version=adapter.model,
        prompt_version=PROMPT_VERSION,
        narration_degraded=degraded,
        tool_calls=budget.used,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
        latency_ms=latency_ms,
    )
