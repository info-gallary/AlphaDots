"""Daily signal panels from the production signal code, plus the comparison signals.

The news signal is computed with :func:`alphadots_quant.decayed_sentiment`, the same
function the live signal engine runs. Each call is handed every reading from the lookback
window *and the day after the decision time*: keeping the future out is the job of the
function's own point-in-time guard, not of this slicing, so the study exercises the guard
on every call and counts what it excluded.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
from numpy.typing import NDArray

from alphadots_quant import SentimentObservation, decayed_sentiment
from alphadots_research.market import FloatArray, Market

DAY_SECONDS = 86_400.0
LOOKBACK_DAYS = 5.0
"""Matches the signal engine's default lookback (ALPHADOTS_SIGNAL_LOOKBACK_SECONDS)."""

BoolArray = NDArray[np.bool_]


@dataclass(frozen=True)
class Panel:
    """A (days, assets) score matrix; ``active`` marks names with any usable evidence."""

    name: str
    score: FloatArray
    active: BoolArray
    future_excluded: int = 0
    """Readings the point-in-time guard refused because they were not yet available."""


def _readings_by_asset(market: Market) -> list[tuple[list[float], list[SentimentObservation]]]:
    per_asset: list[list[SentimentObservation]] = [[] for _ in range(market.config.n_assets)]
    for item in market.news:
        if item.reading is None:  # the sentiment model abstained: no observation exists
            continue
        per_asset[item.asset].append(
            SentimentObservation(
                event_id=item.item_id,
                available_at=item.available_at,
                signed_sentiment=item.reading,
                relevance=item.relevance,
                materiality=item.materiality,
            )
        )
    result = []
    for observations in per_asset:
        observations.sort(key=lambda obs: obs.available_at)
        result.append(([obs.available_at.timestamp() for obs in observations], observations))
    return result


def sentiment_panel(market: Market, tau_days: float, *, decision_offset: int = 0) -> Panel:
    """Decayed sentiment as of each close.

    ``decision_offset=0`` is the honest panel. ``decision_offset=1`` evaluates as of the
    *next* close while labelling the result as today's: the look-ahead bug the study
    uses as its leakage control. Nothing else should ever pass 1.
    """
    days, assets = market.config.n_days, market.config.n_assets
    score = np.zeros((days, assets))
    active = np.zeros((days, assets), dtype=bool)
    excluded = 0
    readings = _readings_by_asset(market)
    lookback = LOOKBACK_DAYS * DAY_SECONDS
    for day in range(days):
        as_of_index = day + decision_offset
        if as_of_index >= days:
            continue
        as_of = market.closes[as_of_index]
        start = (as_of - timedelta(seconds=lookback)).timestamp()
        # Deliberately one day past as_of: the guard inside decayed_sentiment must drop these.
        stop = (as_of + timedelta(days=1)).timestamp()
        for asset, (stamps, observations) in enumerate(readings):
            window = observations[bisect_left(stamps, start) : bisect_right(stamps, stop)]
            if not window:
                continue
            result = decayed_sentiment(
                window,
                as_of=as_of,
                tau_seconds=tau_days * DAY_SECONDS,
                lookback_seconds=lookback,
            )
            excluded += result.excluded_future
            if result.effective_weight > 0:
                score[day, asset] = result.score
                active[day, asset] = True
    name = f"sentiment tau={tau_days:g}d" + (" (look-ahead)" if decision_offset else "")
    return Panel(name=name, score=score, active=active, future_excluded=excluded)


def lagged(panel: Panel, lag_days: int) -> Panel:
    """The same signal ``lag_days`` later: what acting on stale news would look like."""
    score = np.zeros_like(panel.score)
    active = np.zeros_like(panel.active)
    score[lag_days:] = panel.score[:-lag_days]
    active[lag_days:] = panel.active[:-lag_days]
    return Panel(name=f"{panel.name} lagged {lag_days}d", score=score, active=active)


def shuffled(panel: Panel, seed: int) -> Panel:
    """Each day's scores reassigned at random among that day's active names.

    Keeps the signal's distribution and coverage and destroys only which asset it belongs
    to, so any edge left over is noise. The control a real effect has to beat.
    """
    rng = np.random.default_rng(seed)
    score = panel.score.copy()
    for day in range(score.shape[0]):
        names = np.flatnonzero(panel.active[day])
        score[day, names] = score[day, rng.permutation(names)]
    return Panel(name=f"{panel.name} shuffled", score=score, active=panel.active.copy())


def momentum(returns: FloatArray, window: int = 20) -> Panel:
    """Trailing ``window``-day return as of each close: a price-only baseline."""
    cumulative = np.cumsum(returns, axis=0)
    score = np.zeros_like(returns)
    score[window:] = cumulative[window:] - cumulative[:-window]
    active = np.zeros(returns.shape, dtype=bool)
    active[window:] = True
    return Panel(name=f"momentum {window}d", score=score, active=active)
