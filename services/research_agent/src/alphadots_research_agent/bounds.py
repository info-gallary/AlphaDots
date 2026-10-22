"""The bounds every agent run is held to, read from one place."""

from __future__ import annotations

from dataclasses import dataclass

from alphadots_common.config import Settings


@dataclass(frozen=True, slots=True)
class AgentBounds:
    max_tool_calls: int
    max_tokens: int
    timeout_seconds: float
    max_correction_attempts: int
    max_cost_usd: float

    @classmethod
    def from_settings(cls, settings: Settings) -> AgentBounds:
        return cls(
            max_tool_calls=settings.agent_max_tool_calls,
            max_tokens=settings.agent_max_tokens,
            timeout_seconds=settings.agent_timeout_seconds,
            max_correction_attempts=settings.agent_max_correction_attempts,
            max_cost_usd=settings.agent_max_cost_usd,
        )
