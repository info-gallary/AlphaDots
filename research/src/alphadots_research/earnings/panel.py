"""Trading calendar, daily returns and abnormal returns.

The calendar is the set of dates in the Fama-French daily file (NYSE trading days).
``returns[t]`` is the close-to-close return from day ``t-1`` to day ``t``; it is NaN when
either close is missing, so a gap in the price history never becomes a fake zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np

from alphadots_research.earnings.sources import Bars, FactorTable
from alphadots_research.market import FloatArray

FACTOR_NAMES = ("Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom")
MIN_WINDOW_SHARE = 0.9
ESTIMATION = (-260, -21)
MIN_ESTIMATION_DAYS = 120


@dataclass(frozen=True)
class Calendar:
    days: tuple[date, ...]
    position: dict[date, int]
    market: FloatArray
    """Total market return, (Mkt-RF) + RF."""
    rf: FloatArray
    factors: FloatArray
    """Columns in ``FACTOR_NAMES`` order."""


def build_calendar(ff5: FactorTable, mom: FactorTable, start: date, end: date) -> Calendar:
    days = tuple(sorted(d for d in ff5.rows if start <= d <= end and d in mom.rows))
    five = np.array([ff5.rows[d] for d in days])
    names = list(ff5.names)
    market_excess = five[:, names.index("Mkt-RF")]
    rf = five[:, names.index("RF")]
    factors = np.column_stack(
        [five[:, names.index(name)] for name in FACTOR_NAMES[:-1]]
        + [np.array([mom.rows[d][0] for d in days])]
    )
    return Calendar(
        days=days,
        position={d: i for i, d in enumerate(days)},
        market=market_excess + rf,
        rf=rf,
        factors=factors,
    )


def stock_returns(bars: Bars, calendar: Calendar) -> FloatArray:
    price = np.full(len(calendar.days), math.nan)
    for day, close in zip(bars.dates, bars.adjclose, strict=True):
        index = calendar.position.get(day)
        if index is not None and close is not None and close > 0:
            price[index] = close
    returns = np.full_like(price, math.nan)
    returns[1:] = price[1:] / price[:-1] - 1.0
    return returns


def window_sum(values: FloatArray, anchor: int, first: int, last: int) -> float:
    """Sum of ``values[anchor+first ..= anchor+last]`` if at least 90% are present."""
    lo, hi = anchor + first, anchor + last
    if lo < 0 or hi >= values.size:
        return math.nan
    chunk = values[lo : hi + 1]
    present = np.isfinite(chunk)
    if present.sum() < math.ceil(MIN_WINDOW_SHARE * chunk.size):
        return math.nan
    return float(chunk[present].sum())


def factor_abnormal(returns: FloatArray, calendar: Calendar, anchor: int) -> FloatArray | None:
    """Abnormal returns against a 6-factor model (FF5 plus momentum) estimated on days
    ``anchor-260 .. anchor-21``; None with fewer than 120 usable estimation days."""
    lo, hi = anchor + ESTIMATION[0], anchor + ESTIMATION[1]
    if lo < 0:
        return None
    excess = returns - calendar.rf
    rows = np.arange(lo, hi + 1)
    rows = rows[np.isfinite(excess[rows])]
    if rows.size < MIN_ESTIMATION_DAYS:
        return None
    design = np.column_stack([np.ones(rows.size), calendar.factors[rows]])
    coef, *_ = np.linalg.lstsq(design, excess[rows], rcond=None)
    expected = coef[0] + calendar.factors @ coef[1:]
    abnormal: FloatArray = excess - expected
    return abnormal
