"""Returns, abnormal returns, and the calendar-time portfolio's point-in-time rules."""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest

from alphadots_research.earnings.panel import (
    Calendar,
    factor_abnormal,
    stock_returns,
    window_sum,
)
from alphadots_research.earnings.portfolio import (
    MIN_PRIOR_EVENTS,
    Candidate,
    Position,
    run,
    select_positions,
)
from alphadots_research.earnings.sources import Bars


def calendar(n: int, seed: int = 0) -> Calendar:
    rng = np.random.default_rng(seed)
    days = tuple(date(2015, 1, 1) + timedelta(days=i) for i in range(n))
    factors = rng.normal(0, 0.01, size=(n, 6))
    rf = np.full(n, 0.0001)
    return Calendar(days, {d: i for i, d in enumerate(days)}, factors[:, 0] + rf, rf, factors)


def test_returns_are_nan_across_price_gaps() -> None:
    cal = calendar(5)
    bars = Bars(
        "X",
        (cal.days[0], cal.days[1], cal.days[3], cal.days[4]),
        (100.0, 110.0, 120.0, None),
        (0.0,) * 4,
        (0.0,) * 4,
    )
    r = stock_returns(bars, cal)
    assert math.isnan(r[0])
    assert r[1] == pytest.approx(0.1)
    assert math.isnan(r[2])  # no close on day 2
    assert math.isnan(r[3])  # so no return into day 3 either
    assert math.isnan(r[4])  # missing adjusted close


def test_window_sum_needs_ninety_percent_of_days() -> None:
    values = np.ones(30)
    assert window_sum(values, 10, 2, 11) == 10.0
    values[12] = math.nan
    assert window_sum(values, 10, 2, 11) == 9.0  # 9 of 10 days present: enough
    values = np.ones(30)
    values[12:14] = math.nan
    assert math.isnan(window_sum(values, 10, 2, 11))
    assert math.isnan(window_sum(values, 25, 0, 10))  # runs off the end


def test_factor_model_recovers_known_exposures() -> None:
    cal = calendar(400, seed=1)
    rng = np.random.default_rng(2)
    noise = rng.normal(0, 0.002, size=400)
    returns = cal.rf + 0.0002 + cal.factors @ np.array([1.2, 0.5, -0.3, 0.0, 0.1, 0.2]) + noise
    abnormal = factor_abnormal(returns, cal, anchor=350)
    assert abnormal is not None
    assert np.corrcoef(abnormal[300:], noise[300:])[0, 1] > 0.98
    assert factor_abnormal(returns, cal, anchor=100) is None  # estimation window off the start


def candidates_with(signal_after: float) -> list[Candidate]:
    early = [Candidate(firm=i % 5, anchor=i, entry=i + 1, signal=float(i % 10)) for i in range(150)]
    target = Candidate(firm=0, anchor=200, entry=201, signal=9.0)
    late = Candidate(firm=1, anchor=210, entry=211, signal=signal_after)
    return [*early, target, late]


def test_thresholds_never_use_later_events() -> None:
    # Whatever happens after the target event, the target's position is the same.
    first = select_positions(candidates_with(100.0), hold=20)
    second = select_positions(candidates_with(-100.0), hold=20)
    target = Position(firm=0, side=1, start=202, stop=222)
    assert target in first
    assert target in second


def test_too_little_history_means_no_trade() -> None:
    few = [Candidate(firm=0, anchor=i, entry=i, signal=float(i)) for i in range(MIN_PRIOR_EVENTS)]
    assert select_positions(few, hold=5) == []


def test_history_only_events_set_thresholds_but_are_not_traded() -> None:
    history = [
        Candidate(firm=0, anchor=i, entry=i, signal=float(i % 3), eligible=False)
        for i in range(120)
    ]
    assert select_positions(history, hold=5) == []
    traded = select_positions([*history, Candidate(1, 130, 131, 5.0)], hold=5)
    assert traded == [Position(firm=1, side=1, start=132, stop=137)]


def test_portfolio_is_flat_unless_both_sides_are_open() -> None:
    returns = np.full((10, 3), 0.01)
    returns[:, 1] = -0.02
    positions = [Position(0, 1, 2, 6), Position(1, -1, 4, 8)]
    daily = run(positions, returns)
    assert daily.gross[2:4].tolist() == [0.0, 0.0]  # only the long is open
    assert daily.gross[4:6] == pytest.approx([0.03, 0.03])  # +1% long, -(-2%) short
    assert daily.gross[6:8].tolist() == [0.0, 0.0]
    # Opening both legs (2 dollars) and closing them (2 dollars).
    assert daily.turnover.sum() == pytest.approx(4.0)
    assert daily.net(10.0).sum() == pytest.approx(daily.gross.sum() - 4.0 * 0.001)


def test_a_stock_without_a_return_sits_out_that_day() -> None:
    returns = np.array([[0.01, 0.03, -0.01]] * 5)
    returns[2, 1] = math.nan
    positions = [Position(0, 1, 0, 5), Position(1, 1, 0, 5), Position(2, -1, 0, 5)]
    daily = run(positions, returns)
    assert daily.gross[1] == pytest.approx((0.01 + 0.03) / 2 + 0.01)
    assert daily.gross[2] == pytest.approx(0.01 + 0.01)
