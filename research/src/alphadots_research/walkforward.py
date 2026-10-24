"""Expanding-window walk-forward splits with purging, an embargo and an untouched holdout.

Decision day ``d`` uses only information up to close ``d`` and is labelled with the return
to close ``d+1``. A training window that ends at day ``e`` therefore has its last label at
close ``e``. Purging (the label horizon) plus an embargo (for serial dependence) leaves a
gap of ``purge + embargo`` days between the last training label and the first test
decision, and the final ``holdout`` days are touched exactly once, after every choice has
been made on the data before them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from alphadots_research.market import FloatArray


@dataclass(frozen=True)
class Fold:
    index: int
    train: range
    """Decision days used to choose parameters."""
    test: range
    """Out-of-sample decision days, traded with those parameters."""


@dataclass(frozen=True)
class Plan:
    folds: tuple[Fold, ...]
    holdout_train: range
    holdout: range
    purge: int
    embargo: int


def walk_forward(
    n_days: int,
    *,
    first_train: int = 252,
    test_size: int = 63,
    holdout: int = 126,
    purge: int = 1,
    embargo: int = 5,
    warmup: int = 5,
) -> Plan:
    """Split decision days ``0 .. n_days-2`` (the last close has no forward return)."""
    last_decision = n_days - 1
    development_end = last_decision - holdout
    if first_train >= development_end:
        raise ValueError("not enough days for a training window before the holdout")
    gap = purge + embargo
    folds: list[Fold] = []
    start = first_train
    while start < development_end:
        stop = min(start + test_size, development_end)
        folds.append(
            Fold(index=len(folds), train=range(warmup, start - gap), test=range(start, stop))
        )
        start = stop
    return Plan(
        folds=tuple(folds),
        holdout_train=range(warmup, development_end - gap),
        holdout=range(development_end, last_decision),
        purge=purge,
        embargo=embargo,
    )


def mean_ic(ic: FloatArray, days: range) -> float:
    window = ic[days.start : days.stop]
    window = window[np.isfinite(window)]
    return float(window.mean()) if window.size else math.nan


def choose(daily_ic_by_param: dict[float, FloatArray], train: range) -> float:
    """The parameter with the highest mean training IC; ties go to the smallest value."""
    scores = {param: mean_ic(ic, train) for param, ic in daily_ic_by_param.items()}
    finite = {param: score for param, score in scores.items() if math.isfinite(score)}
    if not finite:
        return min(daily_ic_by_param)
    return max(sorted(finite), key=lambda param: finite[param])
