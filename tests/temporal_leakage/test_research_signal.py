"""The research signal panel uses only readings available at each decision time.

Recomputes the decayed sentiment by brute force from the raw news, with the availability
rule written out explicitly, and requires the panel to match it exactly.
"""

from __future__ import annotations

import math

import numpy as np

from alphadots_research.market import MarketConfig, simulate
from alphadots_research.signals import DAY_SECONDS, LOOKBACK_DAYS, sentiment_panel

TAU_DAYS = 1.0


def test_panel_equals_a_brute_force_point_in_time_recomputation() -> None:
    market = simulate(MarketConfig(n_assets=8, n_days=60, seed=5))
    panel = sentiment_panel(market, TAU_DAYS)
    checked = 0
    for day in range(3, 60, 4):
        as_of = market.closes[day]
        for asset in range(8):
            expected = 0.0
            for item in market.news:
                if item.asset != asset or item.reading is None:
                    continue
                age = (as_of - item.available_at).total_seconds()
                if 0 <= age <= LOOKBACK_DAYS * DAY_SECONDS:
                    weight = (
                        item.relevance
                        * item.materiality
                        * math.exp(-age / (TAU_DAYS * DAY_SECONDS))
                    )
                    expected += weight * item.reading
            assert math.isclose(panel.score[day, asset], expected, rel_tol=1e-12, abs_tol=1e-12)
            checked += 1
    assert checked == 15 * 8


def test_the_guard_is_exercised_not_just_the_slicing() -> None:
    market = simulate(MarketConfig(n_assets=8, n_days=60, seed=5))
    panel = sentiment_panel(market, TAU_DAYS)
    assert panel.future_excluded > 0, "readings from after each decision were offered and refused"


def test_the_look_ahead_control_really_does_see_the_future() -> None:
    market = simulate(MarketConfig(n_assets=8, n_days=60, seed=5))
    honest = sentiment_panel(market, TAU_DAYS)
    leaky = sentiment_panel(market, TAU_DAYS, decision_offset=1)
    assert np.array_equal(leaky.score[:-1], sentiment_panel(market, TAU_DAYS).score[1:])
    assert not np.array_equal(leaky.score, honest.score)
