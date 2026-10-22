"""A deterministic stand-in for a real model: no network, same interface, real behaviour.

The research agent does its own quantitative work in plain Python and only asks the
adapter to turn already-computed, already-cited facts into prose (see
``services/research_agent/facts.py``). Every prompt this codebase sends therefore
contains a single fenced ```json``` block holding exactly those facts. The mock adapter
parses that block and renders a small fixed template from it — not a placeholder, an
actual deterministic substitute "narrator" — so CI, local dev with no key, and unit tests
all exercise the same prompt contract a real model sees, with a byte-stable answer.

If a prompt has no JSON block the mock cannot do its job and raises
:class:`~alphadots_llm.adapter.LlmError`, the same failure shape a live provider timing
out would produce, so callers never need a separate code path for "no model configured".
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from alphadots_llm.adapter import ChatMessage, ChatResult, LlmError, LlmErrorReason

_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)
_CHARS_PER_TOKEN = 4
"""A rough, clearly-approximate token estimate for the mock's own fabricated text, used
only so dashboards exercising mock mode see plausible non-zero token counts."""


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _extract_facts(messages: Sequence[ChatMessage]) -> dict[str, Any]:
    user_text = next((m.content for m in reversed(messages) if m.role == "user"), "")
    match = _JSON_BLOCK_RE.search(user_text)
    if match is None:
        raise LlmError(
            LlmErrorReason.INVALID_RESPONSE, "mock adapter found no ```json``` fact block"
        )
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise LlmError(
            LlmErrorReason.INVALID_RESPONSE, f"mock adapter: facts block is not JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise LlmError(
            LlmErrorReason.INVALID_RESPONSE, "mock adapter: facts block is not a JSON object"
        )
    return parsed


def _render_research_brief(facts: dict[str, Any]) -> dict[str, Any]:
    ticker = facts.get("ticker", "the issuer")
    name = facts.get("name", ticker)
    event_count = facts.get("event_count", 0)
    scored_count = facts.get("scored_count", 0)
    positives = facts.get("top_positive", [])
    negatives = facts.get("top_negative", [])
    signal = facts.get("signal") or {}

    def bullets(items: list[dict[str, Any]]) -> list[dict[str, str]]:
        """A citation per item, each pointing at a real evidence ID. Empty when there is
        nothing to cite — never a placeholder "nothing found" entry with no evidence."""
        return [
            {
                "claim": f"{item['headline']} (signed sentiment {item['signed_sentiment']:+.2f}).",
                "evidence_id": item["event_id"],
                "evidence_kind": "event",
            }
            for item in items[:3]
        ]

    stance_text = (
        "abstained (too little evidence to take a stance)"
        if signal.get("abstained")
        else f"{signal.get('stance', 'neutral')} at {signal.get('score_normalized', 0):+.2f}"
    )
    return {
        "business_snapshot": (
            f"{name} ({ticker}) had {event_count} tracked news item(s) in the window, "
            f"{scored_count} of them scored by the sentiment baseline."
        ),
        "bull_case": bullets(positives),
        "bear_case": bullets(negatives),
        "uncertainty_rating": facts.get("suggested_uncertainty", "medium"),
        "quant_summary": f"Decayed-sentiment signal: {stance_text}. Unvalidated, not a forecast.",
    }


def _render_monitor(facts: dict[str, Any]) -> dict[str, Any]:
    """Severity (``status``) is decided in Python before this ever runs and is not part
    of this render — the model, mock or real, only narrates; it never re-decides status."""
    findings = facts.get("findings", [])
    worst = next((f for f in findings if f.get("severity") in ("warning", "critical")), None)
    if worst is None:
        headline = "Pipeline looks healthy."
        summary = "No abstention, backlog or quarantine thresholds were crossed."
    else:
        headline = f"{worst['title']}."
        summary = " ".join(
            f"{item['title']}: {item['detail']}"
            for item in findings
            if item.get("severity") != "ok"
        )
    return {"headline": headline, "summary": summary}


_RENDERERS = {"research_brief": _render_research_brief, "monitor_summary": _render_monitor}


class MockAdapter:
    """Deterministic adapter used whenever no provider key is configured, and in tests."""

    provider = "mock"

    def __init__(self, model: str = "mock-template-v1") -> None:
        self.model = model

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        json_mode: bool,
        max_tokens: int,
        timeout_seconds: float,
    ) -> ChatResult:
        del json_mode, max_tokens, timeout_seconds  # the mock is instant and unbounded by design
        facts = _extract_facts(messages)
        task = facts.get("task")
        renderer = _RENDERERS.get(task) if isinstance(task, str) else None
        if renderer is None:
            raise LlmError(
                LlmErrorReason.INVALID_RESPONSE, f"mock adapter has no template for task {task!r}"
            )
        rendered = renderer(facts)
        content = json.dumps(rendered)
        input_tokens = sum(_estimate_tokens(m.content) for m in messages)
        return ChatResult(
            content=content,
            input_tokens=input_tokens,
            output_tokens=_estimate_tokens(content),
            model=self.model,
            cost_usd=0.0,
        )
