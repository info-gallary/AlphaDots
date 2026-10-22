"""Exponentially decayed sentiment aggregate.

Implements the interpretable candidate from the specification::

    S(i, t) = sum_j relevance(i, j) * materiality(j) * signed_sentiment(j) * exp(-age(j, t) / tau)

This is a *raw sentiment score*. It is not a return forecast, and nothing here says it
predicts anything. ``tau`` is an input: estimating it belongs to training folds only.

The point-in-time invariant is enforced here, in the innermost function, regardless of
what the caller already filtered: an observation with ``available_at > as_of`` never
contributes. It is counted in ``excluded_future`` so that a caller passing future data is
visible rather than silently tolerated.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class SentimentObservation:
    event_id: str
    available_at: datetime
    signed_sentiment: float
    relevance: float
    materiality: float


@dataclass(frozen=True, slots=True)
class DecayContribution:
    event_id: str
    available_at: datetime
    age_seconds: float
    weight: float
    signed_sentiment: float
    contribution: float


@dataclass(frozen=True, slots=True)
class DecayedSentiment:
    score: float
    """S(i, t): the unbounded decayed sum."""
    score_normalized: float
    """S(i, t) divided by the total weight; always within [-1, 1]. Zero when weight is zero."""
    effective_weight: float
    """Sum of relevance * materiality * decay over the contributing observations."""
    contributions: tuple[DecayContribution, ...]
    excluded_future: int
    excluded_stale: int


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def decayed_sentiment(
    observations: Iterable[SentimentObservation],
    *,
    as_of: datetime,
    tau_seconds: float,
    lookback_seconds: float,
) -> DecayedSentiment:
    """Aggregate ``observations`` as of ``as_of`` using only what was available by then.

    Observations exactly at ``as_of`` are included (``available_at <= as_of``); anything
    later is excluded. Observations older than ``lookback_seconds`` are dropped as stale.
    Contributions are returned oldest-first with a deterministic tie-break on ``event_id``.
    """
    if tau_seconds <= 0:
        raise ValueError("tau_seconds must be positive")
    if lookback_seconds <= 0:
        raise ValueError("lookback_seconds must be positive")
    _require_aware(as_of, "as_of")

    contributions: list[DecayContribution] = []
    excluded_future = 0
    excluded_stale = 0
    for observation in observations:
        _require_aware(observation.available_at, "observation.available_at")
        age = (as_of - observation.available_at).total_seconds()
        if age < 0:
            excluded_future += 1
            continue
        if age > lookback_seconds:
            excluded_stale += 1
            continue
        weight = observation.relevance * observation.materiality * math.exp(-age / tau_seconds)
        contributions.append(
            DecayContribution(
                event_id=observation.event_id,
                available_at=observation.available_at,
                age_seconds=age,
                weight=weight,
                signed_sentiment=observation.signed_sentiment,
                contribution=weight * observation.signed_sentiment,
            )
        )

    contributions.sort(key=lambda item: (item.available_at, item.event_id))
    # fsum keeps the result independent of summation order, so replays are bit-stable.
    score = math.fsum(item.contribution for item in contributions)
    effective_weight = math.fsum(item.weight for item in contributions)
    normalized = score / effective_weight if effective_weight > 0 else 0.0
    return DecayedSentiment(
        score=score,
        score_normalized=max(-1.0, min(1.0, normalized)),
        effective_weight=effective_weight,
        contributions=tuple(contributions),
        excluded_future=excluded_future,
        excluded_stale=excluded_stale,
    )
