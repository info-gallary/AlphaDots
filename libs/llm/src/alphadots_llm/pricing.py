"""Cost estimation, opt-in only.

Provider pricing changes over time and this codebase has no way to verify it against the
provider's current rate card. Rather than hard-code a number that could quietly go stale
or be wrong, a rate must be supplied explicitly (``ALPHADOTS_GROQ_PRICE_INPUT_PER_M_USD`` /
``..._OUTPUT_PER_M_USD``). With no rate configured, :func:`estimate_cost_usd` returns
``None``, and every caller must render that as "not estimated" — never as a silent zero.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TokenRate:
    """USD per *million* tokens, matching how providers publish their rate cards."""

    input_per_million_usd: float
    output_per_million_usd: float


def estimate_cost_usd(
    rate: TokenRate | None, input_tokens: int, output_tokens: int
) -> float | None:
    if rate is None:
        return None
    return (
        input_tokens * rate.input_per_million_usd + output_tokens * rate.output_per_million_usd
    ) / 1_000_000
