"""Evaluation: rank IC, a cost-aware long-short portfolio, and honest uncertainty.

Conventions: ``fwd[d]`` is the return from close ``d`` to close ``d+1``, earned by a
position chosen at close ``d`` from information available at close ``d``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np

from alphadots_research.market import FloatArray
from alphadots_research.signals import BoolArray

TRADING_DAYS = 252
MIN_NAMES = 10
"""Fewer active names than this and the day is not traded (and has no IC)."""
EULER_GAMMA = 0.5772156649015329
_NORMAL = NormalDist()


def rankdata(values: FloatArray) -> FloatArray:
    """Ranks starting at 1, ties sharing their average rank (as scipy.stats.rankdata)."""
    sorter = np.argsort(values, kind="mergesort")
    inverse = np.empty_like(sorter)
    inverse[sorter] = np.arange(sorter.size)
    ordered = values[sorter]
    first = np.r_[True, ordered[1:] != ordered[:-1]]
    dense = np.cumsum(first)[inverse]
    bounds = np.r_[np.flatnonzero(first), first.size]
    ranks: FloatArray = 0.5 * (bounds[dense] + bounds[dense - 1] + 1).astype(np.float64)
    return ranks


def spearman(x: FloatArray, y: FloatArray) -> float:
    rx, ry = rankdata(x), rankdata(y)
    rx, ry = rx - rx.mean(), ry - ry.mean()
    denominator = math.sqrt(float((rx**2).sum() * (ry**2).sum()))
    return float((rx * ry).sum() / denominator) if denominator > 0 else math.nan


def daily_ic(score: FloatArray, active: BoolArray, fwd: FloatArray, days: range) -> FloatArray:
    """Cross-sectional Spearman IC per day; NaN on days with too few active names."""
    ic = np.full(len(days), math.nan)
    for row, day in enumerate(days):
        names = active[day] & np.isfinite(fwd[day])
        if names.sum() >= MIN_NAMES:
            ic[row] = spearman(score[day, names], fwd[day, names])
    return ic


def long_short_weights(score: FloatArray, active: BoolArray, quantile: float = 0.3) -> FloatArray:
    """Equal-weight long the top ``quantile`` of active names, short the bottom; gross 1.

    Fewer than MIN_NAMES active names means no position that day (a no-trade day).
    """
    weights = np.zeros_like(score)
    for day in range(score.shape[0]):
        names = np.flatnonzero(active[day])
        if names.size < MIN_NAMES:
            continue
        order = names[np.argsort(score[day, names], kind="mergesort")]
        count = max(1, int(names.size * quantile))
        weights[day, order[-count:]] = 0.5 / count
        weights[day, order[:count]] = -0.5 / count
    return weights


@dataclass(frozen=True)
class Track:
    """Daily results of trading one signal over a contiguous run of decision days."""

    days: range
    ic: FloatArray
    gross: FloatArray
    turnover: FloatArray
    traded: BoolArray
    """Whether any position was held that day (False on no-trade days)."""

    def net(self, cost_bps: float) -> FloatArray:
        """Gross return minus ``cost_bps`` of everything traded that day.

        ``turnover`` is the sum of absolute weight changes, so a switch out of one name
        and into another counts both legs: costs are paid on every dollar bought or sold.
        """
        return self.gross - self.turnover * cost_bps / 10_000.0


def trade(
    score: FloatArray,
    active: BoolArray,
    fwd: FloatArray,
    days: range,
    *,
    previous: FloatArray | None,
) -> tuple[Track, FloatArray]:
    """Trade ``score`` over ``days``; returns the track and the last day's weights.

    ``previous`` carries yesterday's weights in, so turnover is charged across fold
    boundaries exactly as a continuously running strategy would pay it.
    """
    weights = long_short_weights(score[days.start : days.stop], active[days.start : days.stop])
    held = np.nan_to_num(fwd[days.start : days.stop])
    gross = (weights * held).sum(axis=1)
    before = np.vstack(
        [previous if previous is not None else np.zeros(weights.shape[1]), weights[:-1]]
    )
    turnover = np.abs(weights - before).sum(axis=1)
    ic = daily_ic(score, active, fwd, days)
    traded = np.abs(weights).sum(axis=1) > 0
    return Track(days=days, ic=ic, gross=gross, turnover=turnover, traded=traded), weights[-1]


def concat(tracks: list[Track]) -> Track:
    return Track(
        days=range(tracks[0].days.start, tracks[-1].days.stop),
        ic=np.concatenate([t.ic for t in tracks]),
        gross=np.concatenate([t.gross for t in tracks]),
        turnover=np.concatenate([t.turnover for t in tracks]),
        traded=np.concatenate([t.traded for t in tracks]),
    )


def sharpe(returns: FloatArray) -> float:
    """Annualised Sharpe ratio (risk-free rate taken as zero)."""
    deviation = float(returns.std(ddof=1))
    return float(returns.mean()) / deviation * math.sqrt(TRADING_DAYS) if deviation > 0 else 0.0


def bootstrap_sharpe_ci(
    returns: FloatArray, *, seed: int, reps: int = 1000, block: int = 10
) -> tuple[float, float]:
    """95% interval for the Sharpe ratio from a stationary block bootstrap.

    Resampling blocks (mean length ``block``) rather than single days keeps the serial
    dependence a strategy's returns have, so the interval is not falsely narrow.
    """
    rng = np.random.default_rng(seed)
    size = returns.size
    jumps = rng.integers(size, size=(reps, size))
    restart = rng.random((reps, size)) < 1.0 / block
    index = np.empty((reps, size), dtype=np.int64)
    index[:, 0] = jumps[:, 0]
    for slot in range(1, size):
        index[:, slot] = np.where(restart[:, slot], jumps[:, slot], (index[:, slot - 1] + 1) % size)
    samples = returns[index]
    deviation = samples.std(axis=1, ddof=1)
    values = np.divide(
        samples.mean(axis=1), deviation, out=np.zeros(reps), where=deviation > 0
    ) * math.sqrt(TRADING_DAYS)
    low, high = np.percentile(values, [2.5, 97.5])
    return float(low), float(high)


def max_drawdown(returns: FloatArray) -> float:
    """Largest peak-to-trough fall of the compounded equity curve, as a negative fraction."""
    equity = np.cumprod(1.0 + returns)
    peaks = np.maximum.accumulate(np.r_[1.0, equity])[1:]
    return float((equity / peaks - 1.0).min())


def _moments(returns: FloatArray) -> tuple[float, float]:
    centred = returns - returns.mean()
    variance = float((centred**2).mean())
    if variance == 0:
        return 0.0, 3.0
    skew = float((centred**3).mean()) / variance**1.5
    kurtosis = float((centred**4).mean()) / variance**2
    return skew, kurtosis


def probabilistic_sharpe(returns: FloatArray, benchmark: float = 0.0) -> float:
    """P(true Sharpe > ``benchmark``), Bailey & Lopez de Prado (2012). Per-period units."""
    deviation = float(returns.std(ddof=1))
    if deviation == 0 or returns.size < 3:
        return math.nan
    estimate = float(returns.mean()) / deviation
    skew, kurtosis = _moments(returns)
    spread = 1.0 - skew * estimate + (kurtosis - 1.0) / 4.0 * estimate**2
    if spread <= 0:
        return math.nan
    z = (estimate - benchmark) * math.sqrt(returns.size - 1) / math.sqrt(spread)
    return _NORMAL.cdf(z)


def deflated_sharpe(returns: FloatArray, trial_sharpes: list[float]) -> tuple[float, float]:
    """Deflated Sharpe ratio, Bailey & Lopez de Prado (2014).

    ``trial_sharpes`` are the per-period Sharpe ratios of every configuration that was
    tried. The benchmark is the Sharpe the *best* of that many tries would show by luck
    alone; returns (that benchmark, P(true Sharpe > benchmark)).
    """
    trials = len(trial_sharpes)
    if trials < 2:
        return 0.0, probabilistic_sharpe(returns)
    spread = math.sqrt(float(np.var(trial_sharpes, ddof=1)))
    expected_max = spread * (
        (1 - EULER_GAMMA) * _NORMAL.inv_cdf(1 - 1 / trials)
        + EULER_GAMMA * _NORMAL.inv_cdf(1 - 1 / (trials * math.e))
    )
    return expected_max, probabilistic_sharpe(returns, expected_max)


def summarize(track: Track, *, cost_bps: float, seed: int) -> dict[str, float]:
    net = track.net(cost_bps)
    ic = track.ic[np.isfinite(track.ic)]
    low, high = bootstrap_sharpe_ci(net, seed=seed)
    ic_std = float(ic.std(ddof=1)) if ic.size > 1 else math.nan
    return {
        "days": float(net.size),
        "days_traded": float(track.traded.sum()),
        "ann_return": float(net.mean()) * TRADING_DAYS,
        "ann_vol": float(net.std(ddof=1)) * math.sqrt(TRADING_DAYS),
        "sharpe": sharpe(net),
        "sharpe_ci_low": low,
        "sharpe_ci_high": high,
        "gross_sharpe": sharpe(track.gross),
        "max_drawdown": max_drawdown(net),
        "turnover": float(track.turnover.mean()),
        "mean_ic": float(ic.mean()) if ic.size else math.nan,
        "ic_tstat": float(ic.mean()) / ic_std * math.sqrt(ic.size)
        if ic.size > 1 and ic_std > 0
        else math.nan,
        "ic_hit_rate": float((ic > 0).mean()) if ic.size else math.nan,
    }
