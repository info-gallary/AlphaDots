"""The bounded, logged, structured-output LLM call every graph node goes through.

One function, :func:`complete_structured`, is the only place either graph talks to a
model: it enforces the token/time bound on each call, retries a bounded number of times
when the reply does not validate against the expected schema, stops once the run's cost
bound would be exceeded, and folds token/cost usage into the run's :class:`LlmUsage` so
the final result can report exactly what it spent.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from alphadots_common.logging import get_logger
from alphadots_common.metrics import AGENT_COST_USD
from alphadots_llm import ChatMessage, LlmAdapter, LlmError
from alphadots_research_agent.prompts import correction_message

_log = get_logger(__name__)


class AgentAbort(Exception):
    """Raised by any node to make the run abstain. Carries the reason the caller should
    report; never propagates past the ``run_*`` wrapper that owns a graph."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(slots=True)
class LlmUsage:
    """Accumulates across every model call in one run. A mutable object passed by
    reference into graph state, so it still reflects partial work after an abort."""

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    _cost_usd_sum: float = field(default=0.0, repr=False)
    _cost_known: bool = field(default=True, repr=False)

    def add(
        self, *, input_tokens: int, output_tokens: int, cost_usd: float | None, provider: str
    ) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.calls += 1
        if cost_usd is None:
            self._cost_known = False
        else:
            self._cost_usd_sum += cost_usd
            AGENT_COST_USD.labels(provider=provider).inc(cost_usd)

    @property
    def cost_usd(self) -> float | None:
        """``None`` means at least one call's cost could not be priced — shown as "not
        estimated", never understated as a partial sum."""
        return self._cost_usd_sum if self._cost_known else None


def _parse_json_object(text: str) -> dict[str, object]:
    stripped = text.strip().removeprefix("```json").removesuffix("```").strip()
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("response is not a JSON object")
    return parsed


async def complete_structured[T: BaseModel](
    adapter: LlmAdapter,
    messages: list[ChatMessage],
    schema: type[T],
    *,
    max_tokens: int,
    timeout_seconds: float,
    max_correction_attempts: int,
    max_cost_usd: float,
    usage: LlmUsage,
    abort_reason_invalid: str,
    abort_reason_unavailable: str,
    abort_reason_cost: str,
) -> T:
    """Call ``adapter`` and validate the reply against ``schema``, correcting malformed
    JSON up to ``max_correction_attempts`` times. Raises :class:`AgentAbort` rather than
    ever returning an unvalidated or over-budget result."""
    conversation = list(messages)
    last_error = "no attempt made"
    for attempt in range(max_correction_attempts + 1):
        if usage.cost_usd is not None and usage.cost_usd > max_cost_usd:
            raise AgentAbort(
                abort_reason_cost,
                f"estimated spend ${usage.cost_usd:.4f} exceeds the ${max_cost_usd:.4f} bound",
            )
        started = time.perf_counter()
        try:
            result = await adapter.complete(
                conversation, json_mode=True, max_tokens=max_tokens, timeout_seconds=timeout_seconds
            )
        except LlmError as exc:
            _log.warning(
                "llm_call_failed",
                provider=adapter.provider,
                reason=exc.reason,
                attempt=attempt,
                error=str(exc),
            )
            raise AgentAbort(
                abort_reason_unavailable, f"{adapter.provider} call failed: {exc}"
            ) from exc
        usage.add(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            provider=adapter.provider,
        )
        _log.info(
            "llm_call_completed",
            provider=adapter.provider,
            model=result.model,
            attempt=attempt,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
        )
        try:
            return schema.model_validate(_parse_json_object(result.content))
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)[:300]
            conversation = [
                *conversation,
                ChatMessage("user", result.content),
                correction_message(last_error),
            ]

    attempts = max_correction_attempts + 1
    detail = f"model output did not validate after {attempts} attempt(s): {last_error}"
    raise AgentAbort(abort_reason_invalid, detail)
