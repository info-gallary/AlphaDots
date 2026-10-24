"""A synthetic equity market whose news effect is known, so a method can be graded.

Real data cannot tell you whether a backtest is right: you never know the true answer. A
simulation can. Here every news item has a latent tone that moves prices in two ways:

* a **same-day reaction**, priced into the close right after the item is published. A
  strategy that only trades on information available at its decision time cannot earn
  this; one with a look-ahead bug can, which makes it the trap a leakage test needs.
* a **post-availability drift** of known size (``drift``), spread over the following days.
  This is the planted, tradable effect. Setting ``drift=0`` gives the null world, where an
  honest method must find nothing.

The sentiment model sees each item's tone only through a noisy reading (and abstains on
some items entirely), the way a real lexicon or FinBERT score would.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
from numpy.typing import NDArray

# 16:00 New York, as UTC: the decision time on each trading day.
CLOSE = time(20, 0, tzinfo=UTC)

FloatArray = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class MarketConfig:
    n_assets: int = 60
    n_days: int = 756
    start: date = date(2023, 1, 2)
    news_rate: float = 0.12
    """Expected news items per asset per trading day."""
    reading_quality: float = 0.6
    """Correlation between the sentiment model's reading and an item's true tone."""
    abstain_rate: float = 0.25
    """Share of items the sentiment model declines to score; they still move the price."""
    reaction: float = 0.8
    """Same-day move, in daily-volatility units per unit of tone x materiality."""
    drift: float = 0.6
    """Total post-availability drift in the same units: the planted, tradable effect."""
    drift_tau_days: float = 2.0
    drift_horizon_days: int = 10
    market_vol: float = 0.01
    seed: int = 20260425


@dataclass(frozen=True, slots=True)
class NewsItem:
    item_id: str
    asset: int
    published_at: datetime
    available_at: datetime
    tone: float
    """Latent truth. Never visible to a strategy."""
    reading: float | None
    """The sentiment model's signed score in [-1, 1]; None when it abstained."""
    materiality: float
    relevance: float


@dataclass(frozen=True)
class Market:
    config: MarketConfig
    closes: tuple[datetime, ...]
    news: tuple[NewsItem, ...]
    base_returns: FloatArray
    """(days, assets) close-to-close returns without the planted drift; row 0 is zero."""
    drift_returns: FloatArray
    """(days, assets) the planted drift at strength 1; a world adds ``drift * this``."""

    def returns(self, drift: float) -> FloatArray:
        return self.base_returns + drift * self.drift_returns


def trading_closes(start: date, count: int) -> tuple[datetime, ...]:
    """Weekday closes from ``start``. Holidays are ignored: this is a synthetic calendar."""
    closes: list[datetime] = []
    day = start
    while len(closes) < count:
        if day.weekday() < 5:
            closes.append(datetime.combine(day, CLOSE))
        day += timedelta(days=1)
    return tuple(closes)


def simulate(config: MarketConfig) -> Market:
    rng = np.random.default_rng(config.seed)
    days, assets = config.n_days, config.n_assets
    closes = trading_closes(config.start, days)
    close_ts = np.array([close.timestamp() for close in closes])

    beta = rng.uniform(0.7, 1.3, assets)
    vol = rng.uniform(0.012, 0.025, assets)
    market = rng.normal(0.0002, config.market_vol, days)
    base = market[:, None] * beta[None, :] + rng.normal(0.0, 1.0, (days, assets)) * vol[None, :]
    base[0] = 0.0
    drift = np.zeros((days, assets))

    weights = np.exp(-np.arange(config.drift_horizon_days) / config.drift_tau_days)
    weights /= weights.sum()
    counts = rng.poisson(config.news_rate, (days, assets))
    noise_scale = math.sqrt(1.0 - config.reading_quality**2)

    news: list[NewsItem] = []
    for day in range(1, days):
        window = (closes[day] - closes[day - 1]).total_seconds()
        for asset in map(int, np.nonzero(counts[day])[0]):
            for _ in range(int(counts[day, asset])):
                published = closes[day - 1] + timedelta(seconds=float(rng.uniform(0.0, window)))
                available = published + timedelta(seconds=1.0 + float(rng.exponential(45.0)))
                tone = float(rng.normal())
                materiality = float(rng.uniform(0.4, 1.0))
                relevance = float(rng.uniform(0.7, 1.0))
                noise = float(rng.normal())
                abstained = bool(rng.uniform() < config.abstain_rate)
                reading = (
                    None
                    if abstained
                    else math.tanh(config.reading_quality * tone + noise_scale * noise)
                )
                impact = vol[asset] * tone * materiality
                # Published inside (close[day-1], close[day]]: priced into today's close.
                base[day, asset] += config.reaction * impact
                # Tradable only from the first close at or after it became available.
                usable = int(np.searchsorted(close_ts, available.timestamp(), side="left"))
                for lag, weight in enumerate(weights, start=1):
                    if usable + lag < days:
                        drift[usable + lag, asset] += impact * weight
                news.append(
                    NewsItem(
                        item_id=f"sim-{len(news):05d}",
                        asset=asset,
                        published_at=published,
                        available_at=available,
                        tone=tone,
                        reading=reading,
                        materiality=materiality,
                        relevance=relevance,
                    )
                )
    return Market(
        config=config,
        closes=closes,
        news=tuple(news),
        base_returns=base,
        drift_returns=drift,
    )
