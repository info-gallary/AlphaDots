"""The one interface every LLM call in AlphaDots goes through.

Narrow on purpose: a single bounded chat completion, optionally constrained to JSON. No
adapter streams, holds conversation state, or calls tools itself — tool use, retries and
citation checking are the research agent's job, not the model's. That keeps every
provider swappable behind the same contract and keeps cost, token and latency bounded and
logged in exactly one place (:mod:`alphadots_llm.bounded`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["system", "user"]
    content: str


class LlmErrorReason(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH_ERROR = "auth_error"
    HTTP_ERROR = "http_error"
    INVALID_RESPONSE = "invalid_response"
    UNAVAILABLE = "unavailable"


class LlmError(Exception):
    """Raised instead of ever returning a partial or fabricated completion."""

    def __init__(self, reason: LlmErrorReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ChatResult:
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    """Cost in USD, or ``None`` when the configured adapter has no priced rate for this
    model. ``None`` must be shown to the user as "not estimated", never as zero."""
    cost_usd: float | None


class LlmAdapter(Protocol):
    provider: str
    model: str

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        json_mode: bool,
        max_tokens: int,
        timeout_seconds: float,
    ) -> ChatResult:
        """Return one bounded completion, or raise :class:`LlmError`.

        ``timeout_seconds`` bounds this single call; it is the caller's job to also bound
        the number of calls in a run. ``json_mode`` asks the provider for a JSON object
        when it supports one; the caller still validates the result against a schema.
        """
        ...
