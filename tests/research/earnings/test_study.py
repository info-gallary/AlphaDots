"""The whole analysis on a synthetic panel where the answer is planted.

Releases come quarterly; a release's tone surprise moves the stock on days +2..+21. The
analysis must find that drift, and the date-only ("naive") timing must not be able to
see anything the honest timing cannot, except the announcement-day move it trades into.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from alphadots_research.earnings.panel import Calendar
from alphadots_research.earnings.study import Dataset, Event, Plan, _add_surprises, analyse
from alphadots_research.earnings.tone import Tone

FIRMS = 60


def business_days(start: date, end: date) -> tuple[date, ...]:
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return tuple(days)


def tone(net: float) -> Tone:
    hits = 100
    positive = round(hits * (1 + net) / 2)
    return Tone(words=2000, positive=positive, negative=hits - positive)


def synthetic(drift_per_day: float, seed: int = 4) -> Dataset:
    rng = np.random.default_rng(seed)
    days = business_days(date(2014, 1, 1), date(2026, 8, 31))
    n = len(days)
    factors = rng.normal(0, 0.008, size=(n, 6))
    rf = np.full(n, 0.0001)
    cal = Calendar(days, {d: i for i, d in enumerate(days)}, factors[:, 0] + rf, rf, factors)
    returns = cal.market[:, None] + rng.normal(0, 0.012, size=(n, FIRMS))
    first = cal.position[next(d for d in days if d >= date(2015, 1, 5))]
    last = cal.position[next(d for d in days if d >= date(2025, 12, 1))]
    events: list[Event] = []
    for firm in range(FIRMS):
        baseline = rng.normal(0, 0.2)
        firm_events = []
        for t0 in range(first + firm % 60, last, 63):
            net = float(np.clip(baseline + rng.normal(0, 0.25), -0.95, 0.95))
            accepted = datetime.combine(days[t0], datetime.min.time(), UTC) + timedelta(hours=21)
            firm_events.append(
                Event(
                    firm=firm,
                    cik=1000 + firm,
                    accession=f"{firm}-{t0}",
                    accepted_at=accepted,
                    t0=t0,
                    naive=t0 - 1,
                    session="after close",
                    lm=tone(net),
                    lexicon=tone(net / 2),
                )
            )
        _add_surprises(firm_events, Plan())
        for event in firm_events:
            surprise = event.lm_surprise or 0.0
            returns[event.t0, firm] += 0.03 * surprise  # announcement-day reaction
            returns[event.t0 + 2 : event.t0 + 22, firm] += drift_per_day * surprise
        events.extend(firm_events)
    return Dataset(cal, returns, events)


@pytest.fixture(scope="module")
def planted() -> dict:  # type: ignore[type-arg]
    return analyse(synthetic(drift_per_day=0.002))


@pytest.fixture(scope="module")
def null() -> dict:  # type: ignore[type-arg]
    return analyse(synthetic(drift_per_day=0.0))


def test_planted_drift_is_found(planted: dict) -> None:  # type: ignore[type-arg]
    h2 = planted["confirmatory"]["h2"]["development"]["key"]
    assert h2["coef"] > 0
    assert h2["t"] > 3
    assert planted["portfolio"]["primary"]["development"]["sharpe"] > 1


def test_announcement_reaction_is_found_in_both_worlds(planted: dict, null: dict) -> None:  # type: ignore[type-arg]
    for world in (planted, null):
        assert world["confirmatory"]["h1"]["development"]["key"]["t"] > 3


def test_null_world_shows_no_drift(null: dict) -> None:  # type: ignore[type-arg]
    h2 = null["confirmatory"]["h2"]["development"]["key"]
    assert abs(h2["t"]) < 3
    assert null["decision"]["worth_more_work"] is False


def test_date_only_timing_trades_into_the_announcement_and_looks_better(null: dict) -> None:  # type: ignore[type-arg]
    # Every synthetic release lands after the close, so the naive entry is a day early
    # and captures the announcement-day move it could not have known about.
    naive = null["portfolio"]["naive"]["development"]["sharpe"]
    honest = null["portfolio"]["primary"]["development"]["sharpe"]
    assert naive > honest + 1
    assert null["timing"]["naive_early"] == null["timing"]["events"]


def test_results_are_plain_json(planted: dict) -> None:  # type: ignore[type-arg]
    json.dumps(planted, allow_nan=True)  # numpy scalars would raise here


def test_results_carry_no_per_stock_series(planted: dict) -> None:  # type: ignore[type-arg]
    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {k for v in value.values() for k in keys(v)}
        if isinstance(value, list):
            return {k for v in value for k in keys(v)}
        return set()

    assert not keys(planted) & {"returns", "prices", "adjclose", "text", "ticker"}


def test_surprise_uses_only_earlier_releases() -> None:
    accepted = datetime(2020, 1, 1, tzinfo=UTC)
    events = [
        Event(
            firm=0,
            cik=1,
            accession=str(i),
            accepted_at=accepted + timedelta(days=90 * i),
            t0=i,
            naive=i,
            session="after close",
            lm=tone(v),
            lexicon=tone(v),
        )
        for i, v in enumerate([0.1, 0.3, 0.5, -0.5])
    ]
    _add_surprises(list(reversed(events)), Plan())  # order of the input must not matter
    assert events[0].lm_surprise is None
    assert events[1].lm_surprise is None
    assert events[2].lm_surprise == pytest.approx(0.5 - 0.2)
    assert events[3].lm_surprise == pytest.approx(-0.5 - 0.3)


def test_report_renders_from_the_results(planted: dict) -> None:  # type: ignore[type-arg]
    from scripts.run_earnings_study import report  # noqa: PLC0415 - script module

    results = {
        **planted,
        "provenance": {"commit": "abc1234", "downloaded_at": "2026-10-26T00:00:00+00:00"},
    }
    text = report(results)
    assert "## Decision (pre-registered rule)" in text
    assert "Verdict:" in text
    assert not re.search(r"\bnan\b", text, flags=re.IGNORECASE)
