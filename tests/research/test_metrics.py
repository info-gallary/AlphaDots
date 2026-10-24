"""The evaluation arithmetic, checked on inputs whose answers are known by hand."""

from __future__ import annotations

import math

import numpy as np
import pytest

from alphadots_research.metrics import (
    MIN_NAMES,
    bootstrap_sharpe_ci,
    deflated_sharpe,
    long_short_weights,
    max_drawdown,
    rankdata,
    sharpe,
    spearman,
    trade,
)


def test_rankdata_gives_ties_their_average_rank() -> None:
    assert rankdata(np.array([10.0, 20.0, 20.0, 5.0])).tolist() == [2.0, 3.5, 3.5, 1.0]


def test_spearman_is_one_for_any_monotonic_relation_and_minus_one_reversed() -> None:
    x = np.arange(12, dtype=float)
    assert spearman(x, np.exp(x)) == pytest.approx(1.0)
    assert spearman(x, -(x**3)) == pytest.approx(-1.0)


def test_long_short_book_is_dollar_neutral_with_gross_one_and_skips_thin_days() -> None:
    score = np.vstack([np.arange(20.0), np.arange(20.0)])
    active = np.ones_like(score, dtype=bool)
    active[1, MIN_NAMES - 1 :] = False  # day 1 has only MIN_NAMES - 1 names
    weights = long_short_weights(score, active)
    assert weights[0].sum() == pytest.approx(0.0)
    assert np.abs(weights[0]).sum() == pytest.approx(1.0)
    assert weights[0, -1] > 0 > weights[0, 0], "long the highest score, short the lowest"
    assert not weights[1].any(), "too few names: no trade"


def test_turnover_counts_both_legs_and_carries_into_the_next_call() -> None:
    score = np.tile(np.arange(20.0), (3, 1))
    active = np.ones_like(score, dtype=bool)
    fwd = np.zeros_like(score)
    first, last = trade(score, active, fwd, range(0, 2), previous=None)
    assert first.turnover[0] == pytest.approx(1.0), "opening the book from flat trades gross 1"
    assert first.turnover[1] == pytest.approx(0.0), "an unchanged book trades nothing"
    second, _ = trade(score, active, fwd, range(2, 3), previous=last)
    assert second.turnover[0] == pytest.approx(0.0), "yesterday's book carried across the call"


def test_sharpe_and_drawdown_match_hand_arithmetic() -> None:
    returns = np.array([0.01, -0.02, 0.01, 0.02])
    expected = returns.mean() / returns.std(ddof=1) * math.sqrt(252)
    assert sharpe(returns) == pytest.approx(expected)
    # Peak 1.01, trough 1.01 * 0.98: a 2% drawdown.
    assert max_drawdown(returns) == pytest.approx(-0.02)
    assert sharpe(np.zeros(5)) == 0.0


def test_bootstrap_interval_brackets_the_point_estimate_and_is_reproducible() -> None:
    rng = np.random.default_rng(0)
    returns = rng.normal(0.001, 0.01, 500)
    low, high = bootstrap_sharpe_ci(returns, seed=1)
    assert low < sharpe(returns) < high
    assert bootstrap_sharpe_ci(returns, seed=1) == (low, high)


def test_deflated_sharpe_raises_the_bar_as_more_configurations_are_tried() -> None:
    rng = np.random.default_rng(3)
    returns = rng.normal(0.0008, 0.01, 750)
    few_bar, few_p = deflated_sharpe(returns, [0.01, 0.03])
    many_bar, many_p = deflated_sharpe(returns, list(rng.normal(0.02, 0.02, 50)))
    assert many_bar > few_bar
    assert many_p < few_p
