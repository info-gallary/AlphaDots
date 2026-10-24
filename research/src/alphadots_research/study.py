"""The simulation study: can the method find a planted effect, and stay silent without one?

Two worlds share every news item, reading and noise draw, and differ only in ``drift``:

* **planted**: news tone keeps moving prices for a few days after it becomes available.
* **null**: it does not (``drift = 0``). Same-day reactions still happen in both.

In each world the news signal (production ``decayed_sentiment``, ``tau`` chosen on
training folds only) is traded walk-forward against four comparisons:

* **shuffled**: the same scores reassigned at random among names each day.
* **lagged**: the same signal acted on five days late.
* **momentum**: a price-only baseline (trailing 20-day return).
* **look-ahead**: the news signal computed as of the *next* close - a deliberate bug,
  included to show how much a leak would be worth in a world with nothing to find.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from alphadots_research.market import FloatArray, Market, MarketConfig, simulate
from alphadots_research.metrics import (
    MIN_NAMES,
    Track,
    concat,
    daily_ic,
    deflated_sharpe,
    sharpe,
    summarize,
    trade,
)
from alphadots_research.signals import Panel, lagged, momentum, sentiment_panel, shuffled
from alphadots_research.walkforward import Plan, choose, mean_ic, walk_forward

STRATEGIES = {
    "sentiment": "News sentiment (point-in-time)",
    "shuffled": "Shuffled sentiment (control)",
    "lagged": "Sentiment acted on 5 days late",
    "momentum": "Price momentum (20 days)",
    "lookahead": "Look-ahead bug (deliberately wrong)",
}


@dataclass(frozen=True)
class StudyConfig:
    market: MarketConfig = field(default_factory=MarketConfig)
    tau_grid_days: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 3.0, 5.0)
    lookahead_tau_days: float = 1.0
    lag_days: int = 5
    momentum_days: int = 20
    cost_bps: float = 10.0
    cost_grid_bps: tuple[float, ...] = (0.0, 5.0, 10.0, 20.0, 40.0)
    first_train_days: int = 252
    test_days: int = 63
    holdout_days: int = 126
    purge_days: int = 1
    embargo_days: int = 5
    seed: int = 7


def _forward(returns: FloatArray) -> FloatArray:
    fwd = np.full_like(returns, math.nan)
    fwd[:-1] = returns[1:]
    return fwd


def _quintiles(panels: list[tuple[Panel, range]], fwd: FloatArray) -> list[float]:
    """Mean next-day return (bps) of each signal quintile, averaged over traded days."""
    buckets: list[list[float]] = [[] for _ in range(5)]
    for panel, days in panels:
        for day in days:
            names = np.flatnonzero(panel.active[day] & np.isfinite(fwd[day]))
            if names.size < MIN_NAMES:
                continue
            order = names[np.argsort(panel.score[day, names], kind="mergesort")]
            for bucket, chunk in enumerate(np.array_split(order, 5)):
                buckets[bucket].append(float(fwd[day, chunk].mean()))
    return [float(np.mean(values)) * 10_000.0 for values in buckets]


def _world(
    market: Market,
    config: StudyConfig,
    plan: Plan,
    *,
    drift: float,
    panels: dict[float, Panel],
    lookahead: Panel,
) -> dict[str, Any]:
    returns = market.returns(drift)
    fwd = _forward(returns)
    decision_days = range(market.config.n_days)
    ic_by_tau = {tau: daily_ic(p.score, p.active, fwd, decision_days) for tau, p in panels.items()}
    price = momentum(returns, config.momentum_days)

    def signals(tau: float, salt: int) -> dict[str, Panel]:
        chosen = panels[tau]
        return {
            "sentiment": chosen,
            "shuffled": shuffled(chosen, seed=config.seed * 1000 + salt),
            "lagged": lagged(chosen, config.lag_days),
            "momentum": price,
            "lookahead": lookahead,
        }

    tracks: dict[str, list[Track]] = {key: [] for key in STRATEGIES}
    previous: dict[str, FloatArray | None] = dict.fromkeys(STRATEGIES)
    folds = []
    chosen_panels: list[tuple[Panel, range]] = []
    for fold in plan.folds:
        tau = choose(ic_by_tau, fold.train)
        for key, panel in signals(tau, fold.index).items():
            track, previous[key] = trade(
                panel.score, panel.active, fwd, fold.test, previous=previous[key]
            )
            tracks[key].append(track)
        chosen_panels.append((panels[tau], fold.test))
        folds.append(
            {
                "index": fold.index,
                "train_start": market.closes[fold.train.start].date().isoformat(),
                "train_end": market.closes[fold.train.stop - 1].date().isoformat(),
                "test_start": market.closes[fold.test.start].date().isoformat(),
                "test_end": market.closes[fold.test.stop - 1].date().isoformat(),
                "tau_days": tau,
                "train_ic": mean_ic(ic_by_tau[tau], fold.train),
                "test_ic": mean_ic(ic_by_tau[tau], fold.test),
            }
        )
    oos = {key: concat(parts) for key, parts in tracks.items()}

    holdout_tau = choose(ic_by_tau, plan.holdout_train)
    holdout: dict[str, Track] = {}
    for key, panel in signals(holdout_tau, len(plan.folds)).items():
        holdout[key], _ = trade(
            panel.score, panel.active, fwd, plan.holdout, previous=previous[key]
        )

    # Every tau tried, each traded unchanged over the out-of-sample window: the trials a
    # deflated Sharpe ratio has to account for.
    oos_days = range(plan.folds[0].test.start, plan.folds[-1].test.stop)
    trial_sharpes = []
    for tau in config.tau_grid_days:
        trial, _ = trade(panels[tau].score, panels[tau].active, fwd, oos_days, previous=None)
        net = trial.net(config.cost_bps)
        trial_sharpes.append(float(net.mean() / net.std(ddof=1)) if net.std(ddof=1) > 0 else 0.0)
    sentiment_net = oos["sentiment"].net(config.cost_bps)
    benchmark, dsr = deflated_sharpe(sentiment_net, trial_sharpes)

    realized = [market.closes[day + 1].date().isoformat() for day in [*oos_days, *plan.holdout]]
    strategies = {}
    for index, (key, label) in enumerate(STRATEGIES.items()):
        net = np.concatenate([oos[key].net(config.cost_bps), holdout[key].net(config.cost_bps)])
        strategies[key] = {
            "label": label,
            "oos": summarize(oos[key], cost_bps=config.cost_bps, seed=config.seed + index),
            "holdout": summarize(
                holdout[key], cost_bps=config.cost_bps, seed=config.seed + 50 + index
            ),
            "equity": (np.cumprod(1.0 + net) - 1.0).tolist(),
        }
    return {
        "drift": drift,
        "folds": folds,
        "holdout_tau_days": holdout_tau,
        "dates": realized,
        "holdout_start_index": len(oos_days),
        "strategies": strategies,
        "quintiles_bps": _quintiles(chosen_panels, fwd),
        "cost_sensitivity": [
            {"bps": bps, "sharpe": sharpe(oos["sentiment"].net(bps))}
            for bps in config.cost_grid_bps
        ],
        "deflated_sharpe": {
            "trials": len(trial_sharpes),
            "benchmark_annualized": benchmark * math.sqrt(252),
            "probability": dsr,
        },
    }


def run(config: StudyConfig | None = None) -> dict[str, Any]:
    config = config or StudyConfig()
    market = simulate(config.market)
    panels = {tau: sentiment_panel(market, tau) for tau in config.tau_grid_days}
    lookahead = sentiment_panel(market, config.lookahead_tau_days, decision_offset=1)
    plan = walk_forward(
        config.market.n_days,
        first_train=config.first_train_days,
        test_size=config.test_days,
        holdout=config.holdout_days,
        purge=config.purge_days,
        embargo=config.embargo_days,
    )
    readings = sum(item.reading is not None for item in market.news)
    honest = panels[config.lookahead_tau_days]
    return {
        "kind": "simulation",
        "config": {
            **{k: v for k, v in asdict(config).items() if k != "market"},
            "market": {**asdict(config.market), "start": config.market.start.isoformat()},
            "min_names": MIN_NAMES,
            "folds": len(plan.folds),
        },
        "news": {
            "items": len(market.news),
            "readings": readings,
            "abstained": len(market.news) - readings,
        },
        "pit_guard": {
            "offered_before_available": honest.future_excluded,
            "readings": readings,
            "lookahead_offered_before_available": lookahead.future_excluded,
        },
        "worlds": {
            "planted": _world(
                market, config, plan, drift=config.market.drift, panels=panels, lookahead=lookahead
            ),
            "null": _world(market, config, plan, drift=0.0, panels=panels, lookahead=lookahead),
        },
    }
