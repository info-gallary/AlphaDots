"""Calendar-time long-short portfolio of earnings events.

Each event becomes a position only if its signal is extreme relative to events that came
*before* it: the 33rd and 67th percentiles of signals whose anchor day falls in the
previous 252 trading days. One dollar long the open longs, one dollar short the open
shorts, equal-weighted, rebalanced daily; flat on days when either side is empty.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

import numpy as np

from alphadots_research.market import FloatArray

LOOKBACK_DAYS = 252
MIN_PRIOR_EVENTS = 100
LOW_PERCENTILE, HIGH_PERCENTILE = 33.0, 67.0


@dataclass(frozen=True, slots=True)
class Candidate:
    firm: int
    anchor: int
    """Day whose information decides the trade; thresholds use events strictly before it."""
    entry: int
    """Index of the close at which the position is opened."""
    signal: float
    eligible: bool = True
    """False for history-only events (they set thresholds but are never traded)."""


@dataclass(frozen=True, slots=True)
class Position:
    firm: int
    side: int
    start: int
    """First return day held (the day after entry)."""
    stop: int
    """One past the last return day held."""


def select_positions(candidates: list[Candidate], hold: int) -> list[Position]:
    ordered = sorted(candidates, key=lambda c: (c.anchor, c.firm))
    anchors = [c.anchor for c in ordered]
    signals = np.array([c.signal for c in ordered])
    positions = []
    for candidate in ordered:
        if not candidate.eligible:
            continue
        lo = bisect_left(anchors, candidate.anchor - LOOKBACK_DAYS)
        hi = bisect_left(anchors, candidate.anchor)
        if hi - lo < MIN_PRIOR_EVENTS:
            continue
        low, high = np.percentile(signals[lo:hi], [LOW_PERCENTILE, HIGH_PERCENTILE])
        side = 1 if candidate.signal >= high else -1 if candidate.signal <= low else 0
        if side:
            start = candidate.entry + 1
            positions.append(Position(candidate.firm, side, start, start + hold))
    return positions


@dataclass(frozen=True)
class Daily:
    gross: FloatArray
    turnover: FloatArray
    """Sum of absolute changes in weight (both legs), so costs are per dollar traded."""
    longs: FloatArray
    shorts: FloatArray

    def net(self, cost_bps: float) -> FloatArray:
        return self.gross - self.turnover * cost_bps / 10_000.0


def run(positions: list[Position], returns: FloatArray) -> Daily:
    """``returns`` is (days, firms). A stock with no return on a day sits out that day."""
    days, firms = returns.shape
    open_long = np.zeros((days, firms))
    open_short = np.zeros((days, firms))
    for position in positions:
        target = open_long if position.side > 0 else open_short
        target[position.start : min(position.stop, days), position.firm] += 1.0
    present = np.isfinite(returns)
    held = np.nan_to_num(returns)
    open_long *= present
    open_short *= present
    longs, shorts = open_long.sum(axis=1), open_short.sum(axis=1)
    trading = (longs > 0) & (shorts > 0)
    weights = np.zeros((days, firms))
    weights[trading] = (
        open_long[trading] / longs[trading, None] - open_short[trading] / shorts[trading, None]
    )
    gross = (weights * held).sum(axis=1)
    before = np.vstack([np.zeros(firms), weights[:-1]])
    turnover = np.abs(weights - before).sum(axis=1)
    return Daily(gross=gross, turnover=turnover, longs=longs * trading, shorts=shorts * trading)
