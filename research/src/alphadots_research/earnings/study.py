"""The earnings press-release tone study, as pre-registered in
``docs/research/earnings-tone-preregistration.md``.

``load`` reads every input from the download cache (offline, so a run is reproducible);
``analyse`` turns the dataset into the published aggregates. Nothing per-stock leaves
``analyse``: no prices, returns or text, only statistics over many events.
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import numpy as np

from alphadots_research.earnings.events import (
    MARKET_CLOSE,
    candidates,
    drop_repeats,
    event_day,
    naive_day,
    new_york,
)
from alphadots_research.earnings.http import PoliteClient
from alphadots_research.earnings.panel import (
    Calendar,
    build_calendar,
    factor_abnormal,
    stock_returns,
    window_sum,
)
from alphadots_research.earnings.portfolio import Candidate, Daily, run, select_positions
from alphadots_research.earnings.sources import (
    FF5_DAILY,
    MOM_DAILY,
    Dictionary,
    Filing,
    company_tickers,
    daily_bars,
    frames,
    french_daily,
    index_url,
    lm_dictionary,
    narrative_text,
    parse_press_release_href,
    submissions,
)
from alphadots_research.earnings.stats import Fit, newey_west, ols, winsorize, zscore_by_group
from alphadots_research.earnings.tone import MIN_WORDS, Tone, lexicon_tone, lm_tone
from alphadots_research.earnings.universe import FLOAT_PERIODS, Rejected, select
from alphadots_research.market import FloatArray
from alphadots_research.metrics import (
    TRADING_DAYS,
    bootstrap_sharpe_ci,
    deflated_sharpe,
    max_drawdown,
    sharpe,
)

MARKET_OPEN = time(9, 30)
TEXT_PIPELINE_VERSION = "tone-v1"
"""Bump when text extraction or scoring changes, to invalidate the derived-feature cache."""


@dataclass(frozen=True)
class Plan:
    history_start: date = date(2015, 1, 1)
    test_start: date = date(2016, 1, 1)
    holdout_start: date = date(2024, 1, 1)
    end: date = date(2025, 12, 31)
    calendar_start: date = date(2014, 1, 1)
    calendar_end: date = date(2026, 8, 31)
    reaction: tuple[int, int] = (0, 1)
    drift: tuple[int, int] = (2, 21)
    runup: tuple[int, int] = (-21, -2)
    hold: int = 20
    entry_offset: int = 1
    cost_bps: float = 10.0
    holds: tuple[int, ...] = (5, 20, 60)
    costs: tuple[float, ...] = (0.0, 5.0, 10.0, 20.0)
    prior_events: int = 4
    min_prior_events: int = 2
    nw_lags: int = 20
    path: tuple[int, int] = (-10, 60)
    seed: int = 20260426


PLAN = Plan()


@dataclass
class Event:
    firm: int
    """Column in the returns matrix, or -1 when the firm has no price history."""
    cik: int
    accession: str
    accepted_at: datetime
    t0: int
    naive: int
    session: str
    lm: Tone
    lexicon: Tone
    lm_surprise: float | None = None
    lexicon_surprise: float | None = None


@dataclass
class Dataset:
    calendar: Calendar
    returns: FloatArray
    """(days, priced firms)."""
    events: list[Event]
    funnel: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    unpriced: list[str] = field(default_factory=list)


def session(accepted_at: datetime, calendar: Calendar) -> str:
    local = new_york(accepted_at)
    if local.date() not in calendar.position:
        return "non-trading day"
    if local.time() < MARKET_OPEN:
        return "pre-market"
    return "market hours" if local.time() < MARKET_CLOSE else "after close"


def _features(
    sec: PoliteClient, accession_url: str, scorer: Callable[[str], tuple[Tone, Tone]]
) -> dict[str, Any]:
    index = sec.get(accession_url)
    href = None if index is None else parse_press_release_href(index.decode("utf-8", "replace"))
    document = sec.get(href) if href else None
    if document is None:
        return {"status": "no exhibit"}
    lm, lexicon = scorer(narrative_text(document))
    return {
        "status": "ok",
        "lm": [lm.words, lm.positive, lm.negative],
        "lexicon": [lexicon.words, lexicon.positive, lexicon.negative],
    }


_WORKER: dict[str, Any] = {}


def _init_worker(cache_dir: Path, dictionary: Dictionary) -> None:
    _WORKER["client"] = PoliteClient(cache_dir, "", {}, offline=True)
    _WORKER["dictionary"] = dictionary


def _worker_features(url: str) -> dict[str, Any]:
    dictionary = _WORKER["dictionary"]
    return _features(
        _WORKER["client"], url, lambda text: (lm_tone(text, dictionary), lexicon_tone(text))
    )


def _extract_features(
    sec: PoliteClient,
    dictionary: Dictionary,
    filings: list[Filing],
    cache: dict[str, dict[str, Any]],
    workers: int | None,
) -> None:
    """Fill ``cache`` with text features for every filing not already in it."""
    missing = [filing for filing in filings if filing.accession not in cache]
    if not missing:
        return
    with ProcessPoolExecutor(
        max_workers=workers or os.cpu_count(),
        initializer=_init_worker,
        initargs=(sec.cache_dir, dictionary),
    ) as pool:
        urls = [index_url(filing) for filing in missing]
        for filing, features in zip(
            missing, pool.map(_worker_features, urls, chunksize=16), strict=True
        ):
            cache[filing.accession] = features


def load(
    sec: PoliteClient,
    web: PoliteClient,
    derived: Path,
    plan: Plan = PLAN,
    *,
    workers: int | None = None,
) -> Dataset:
    """Build the event dataset from cached downloads. Text features are extracted in a
    process pool (HTML parsing dominates) and kept in ``derived``, keyed by accession
    number, so re-runs skip it."""
    calendar = build_calendar(
        french_daily(web, FF5_DAILY),
        french_daily(web, MOM_DAILY),
        plan.calendar_start,
        plan.calendar_end,
    )
    dictionary = lm_dictionary(web)
    floats = [f for p in FLOAT_PERIODS for f in frames(sec, "dei", "EntityPublicFloat", p)]
    assets = [f for p in FLOAT_PERIODS for f in frames(sec, "us-gaap", "Assets", p)]
    chosen, rejected = select(floats, assets)
    tickers = company_tickers(sec)
    cache_file = derived / f"{TEXT_PIPELINE_VERSION}.json"
    cache: dict[str, dict[str, Any]] = (
        json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    )

    releases_by_cik = {
        member.cik: candidates(
            submissions(sec, member.cik, plan.history_start), plan.history_start, plan.end
        )
        for member in chosen
    }
    _extract_features(
        sec, dictionary, [f for fs in releases_by_cik.values() for f in fs], cache, workers
    )

    columns: list[FloatArray] = []
    events: list[Event] = []
    unpriced: list[str] = []
    counts: dict[str, int] = defaultdict(int)
    for member in chosen:
        ticker = tickers.get(member.cik)
        counts["ticker"] += ticker is not None
        bars = daily_bars(web, ticker) if ticker else None
        firm = -1
        if bars is not None:
            columns.append(stock_returns(bars, calendar))
            firm = len(columns) - 1
            counts["priced"] += 1
        else:
            unpriced.append(member.name)
        usable: list[Event] = []
        for filing in releases_by_cik[member.cik]:
            counts["releases"] += 1
            features = cache[filing.accession]
            if features["status"] != "ok":
                continue
            counts["exhibit"] += 1
            lm, lexicon = Tone(*features["lm"]), Tone(*features["lexicon"])
            t0 = event_day(filing.accepted_at, calendar.days)
            naive = naive_day(filing.filing_date, calendar.days)
            if lm.words < MIN_WORDS or lm.net is None or t0 is None or naive is None:
                continue
            counts["scored"] += 1
            usable.append(
                Event(
                    firm=firm,
                    cik=member.cik,
                    accession=filing.accession,
                    accepted_at=filing.accepted_at,
                    t0=calendar.position[t0],
                    naive=calendar.position[naive],
                    session=session(filing.accepted_at, calendar),
                    lm=lm,
                    lexicon=lexicon,
                )
            )
        keep = drop_repeats([calendar.days[e.t0] for e in usable], calendar.days)
        kept = [event for event, flag in zip(usable, keep, strict=True) if flag]
        counts["unique"] += len(kept)
        _add_surprises(kept, plan)
        events.extend(kept)

    derived.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
    returns = np.column_stack(columns) if columns else np.empty((len(calendar.days), 0))
    funnel = [
        {
            "step": "Largest US filers by 2014 public float",
            "count": len(chosen),
            "note": f"after removing {len(rejected)} XBRL unit errors",
        },
        {
            "step": "Still listed today (SEC ticker)",
            "count": counts["ticker"],
            "note": "acquired and delisted firms drop out here",
        },
        {"step": "With Yahoo price history", "count": counts["priced"], "note": ""},
        {
            "step": "Earnings 8-Ks (Item 2.02), 2015-2025",
            "count": counts["releases"],
            "note": "all selected firms",
        },
        {"step": "With a press-release exhibit (EX-99)", "count": counts["exhibit"], "note": ""},
        {"step": "At least 100 words and some tone words", "count": counts["scored"], "note": ""},
        {"step": "After removing repeat releases", "count": counts["unique"], "note": ""},
    ]
    return Dataset(calendar, returns, events, funnel, rejected, sorted(unpriced))


def _add_surprises(events: list[Event], plan: Plan) -> None:
    """Tone minus the firm's mean tone over its previous (up to 4) releases."""
    lm_history: list[float] = []
    lexicon_history: list[float] = []
    for event in sorted(events, key=lambda e: e.accepted_at):
        recent = lm_history[-plan.prior_events :]
        if len(recent) >= plan.min_prior_events and event.lm.net is not None:
            event.lm_surprise = event.lm.net - float(np.mean(recent))
        lexicon_recent = lexicon_history[-plan.prior_events :]
        if len(lexicon_recent) >= plan.min_prior_events and event.lexicon.net is not None:
            event.lexicon_surprise = event.lexicon.net - float(np.mean(lexicon_recent))
        if event.lm.net is not None:
            lm_history.append(event.lm.net)
        if event.lexicon.net is not None:
            lexicon_history.append(event.lexicon.net)


# ---------------------------------------------------------------------------- analysis


@dataclass
class Row:
    event: Event
    year: int
    holdout: bool
    reaction: float
    drift: float
    runup: float
    drift_short: float
    drift_long: float
    ff_reaction: float
    ff_drift: float
    path: FloatArray


def _rows(data: Dataset, plan: Plan) -> list[Row]:
    calendar = data.calendar
    rows = []
    for event in data.events:
        day = calendar.days[event.t0]
        if event.firm < 0 or not plan.test_start <= day <= plan.end:
            continue
        returns = data.returns[:, event.firm]
        abnormal = returns - calendar.market
        factor = factor_abnormal(returns, calendar, event.t0)
        first, last = plan.path
        lo, hi = event.t0 + first, event.t0 + last
        path = np.full(last - first + 1, math.nan)
        if lo >= 0 and hi < abnormal.size:
            path = abnormal[lo : hi + 1].copy()
        rows.append(
            Row(
                event=event,
                year=day.year,
                holdout=day >= plan.holdout_start,
                reaction=window_sum(abnormal, event.t0, *plan.reaction),
                drift=window_sum(abnormal, event.t0, *plan.drift),
                runup=window_sum(abnormal, event.t0, *plan.runup),
                drift_short=window_sum(abnormal, event.t0, plan.drift[0], plan.drift[0] + 4),
                drift_long=window_sum(abnormal, event.t0, plan.drift[0], plan.drift[0] + 59),
                ff_reaction=math.nan
                if factor is None
                else window_sum(factor, event.t0, *plan.reaction),
                ff_drift=math.nan if factor is None else window_sum(factor, event.t0, *plan.drift),
                path=path,
            )
        )
    return rows


def _fit_dict(fit: Fit, key: str) -> dict[str, Any]:
    return {
        "n": fit.n,
        "r2": fit.r2,
        "coefficients": [{"name": name, **fit.row(name)} for name in fit.names],
        "key": fit.row(key),
    }


def _regress(
    rows: list[Row],
    outcome: Callable[[Row], float],
    tone: Callable[[Row], float | None],
    controls: dict[str, Callable[[Row], float]],
    *,
    winsorized: bool = True,
) -> dict[str, Any] | None:
    picked = [
        r
        for r in rows
        if tone(r) is not None
        and math.isfinite(outcome(r))
        and all(math.isfinite(c(r)) for c in controls.values())
    ]
    if len(picked) < 30:
        return None
    y = np.array([outcome(r) for r in picked])
    if winsorized:
        y = winsorize(y)
    raw_tone = np.array([float(tone(r) or 0.0) for r in picked])
    years = np.array([r.year for r in picked], dtype=np.int64)
    columns = {"tone": zscore_by_group(raw_tone, years)}
    columns.update({name: np.array([c(r) for r in picked]) for name, c in controls.items()})
    firms = np.array([r.event.cik for r in picked], dtype=np.int64)
    dates = np.array([r.event.t0 for r in picked], dtype=np.int64)
    return _fit_dict(ols(y, columns, [firms, dates]), "tone")


def _samples(rows: list[Row]) -> dict[str, list[Row]]:
    return {
        "development": [r for r in rows if not r.holdout],
        "holdout": [r for r in rows if r.holdout],
    }


def _signal(name: str) -> Callable[[Event], float | None]:
    return {
        "lm_surprise": lambda e: e.lm_surprise,
        "lm_level": lambda e: e.lm.net,
        "lexicon_surprise": lambda e: e.lexicon_surprise,
    }[name]


def _portfolio(data: Dataset, plan: Plan, signal: str, hold: int, *, naive: bool = False) -> Daily:
    get = _signal(signal)
    candidates_: list[Candidate] = []
    for event in data.events:
        value = get(event)
        if event.firm < 0 or value is None:
            continue
        day = data.calendar.days[event.t0]
        anchor = event.naive if naive else event.t0
        entry = event.naive if naive else event.t0 + plan.entry_offset
        candidates_.append(
            Candidate(
                firm=event.firm,
                anchor=anchor,
                entry=entry,
                signal=value,
                eligible=plan.test_start <= day <= plan.end,
            )
        )
    return run(select_positions(candidates_, hold), data.returns)


def _periods(data: Dataset, plan: Plan) -> dict[str, range]:
    """Return days by period. The holdout runs until the last holdout position (an event
    on the plan's final day, entered at t0+1 and held ``plan.hold`` days) has closed."""
    days = data.calendar.days
    first = next(i for i, d in enumerate(days) if d >= plan.test_start)
    split = next(i for i, d in enumerate(days) if d >= plan.holdout_start)
    final = max(i for i, d in enumerate(days) if d <= plan.end)
    stop = min(len(days), final + plan.entry_offset + plan.hold + 1)
    return {"development": range(first, split), "holdout": range(split, stop)}


def _summary(
    data: Dataset, net: FloatArray, daily: Daily, days: range, plan: Plan
) -> dict[str, Any]:
    series = net[days.start : days.stop]
    factors = data.calendar.factors[days.start : days.stop]
    low, high = bootstrap_sharpe_ci(series, seed=plan.seed)
    alpha = newey_west(
        series,
        {name: factors[:, i] for i, name in enumerate(("mkt", "smb", "hml", "rmw", "cma", "mom"))},
        plan.nw_lags,
    )
    traded = daily.longs[days.start : days.stop] > 0
    return {
        "start": data.calendar.days[days.start].isoformat(),
        "end": data.calendar.days[days.stop - 1].isoformat(),
        "days": len(series),
        "days_traded": int(traded.sum()),
        "ann_return": float(series.mean()) * TRADING_DAYS,
        "ann_vol": float(series.std(ddof=1)) * math.sqrt(TRADING_DAYS),
        "sharpe": sharpe(series),
        "sharpe_ci_low": low,
        "sharpe_ci_high": high,
        "max_drawdown": max_drawdown(series),
        "turnover": float(daily.turnover[days.start : days.stop].mean()),
        "avg_longs": float(daily.longs[days.start : days.stop][traded].mean())
        if traded.any()
        else 0.0,
        "avg_shorts": float(daily.shorts[days.start : days.stop][traded].mean())
        if traded.any()
        else 0.0,
        "alpha_ann": float(alpha.coef[0]) * TRADING_DAYS,
        "alpha_t": float(alpha.t[0]),
    }


def _paths(rows: list[Row], plan: Plan) -> dict[str, Any]:
    picked = [r for r in rows if r.event.lm_surprise is not None]
    raw = np.array([r.event.lm_surprise or 0.0 for r in picked])
    years = np.array([r.year for r in picked], dtype=np.int64)
    z = zscore_by_group(raw, years)
    low, high = np.quantile(z, [1 / 3, 2 / 3])
    groups = {
        "Most negative third": z <= low,
        "Middle third": (z > low) & (z < high),
        "Most positive third": z >= high,
    }
    offsets = list(range(plan.path[0], plan.path[1] + 1))
    out = []
    for label, members in groups.items():
        paths = np.array([r.path for r, m in zip(picked, members, strict=True) if m])
        cumulative = np.cumsum(np.nan_to_num(paths), axis=1)
        # Anchor each event's path at zero on day -1, so day 0 onwards is the response to
        # the release and earlier days show the run-up into it.
        cumulative -= cumulative[:, [offsets.index(-1)]]
        mean = cumulative.mean(axis=0)
        error = 1.96 * cumulative.std(axis=0, ddof=1) / math.sqrt(len(cumulative))
        reaction = np.array([r.reaction for r, m in zip(picked, members, strict=True) if m])
        drift = np.array([r.drift for r, m in zip(picked, members, strict=True) if m])
        out.append(
            {
                "label": label,
                "n": len(cumulative),
                "mean": mean.tolist(),
                "low": (mean - error).tolist(),
                "high": (mean + error).tolist(),
                "reaction": _mean_ci(reaction),
                "drift": _mean_ci(drift),
            }
        )
    return {"offsets": offsets, "groups": out}


def _mean_ci(values: FloatArray) -> dict[str, float]:
    values = values[np.isfinite(values)]
    mean = float(values.mean())
    error = 1.96 * float(values.std(ddof=1)) / math.sqrt(values.size)
    return {"mean": mean, "low": mean - error, "high": mean + error, "n": float(values.size)}


def _tone_index(data: Dataset) -> list[dict[str, Any]]:
    by_quarter: dict[str, list[float]] = defaultdict(list)
    for event in data.events:
        local = new_york(event.accepted_at).date()
        if event.lm.net is not None:
            by_quarter[f"{local.year}Q{(local.month - 1) // 3 + 1}"].append(event.lm.net)
    return [
        {"quarter": q, "tone": float(np.mean(v)), "n": len(v)}
        for q, v in sorted(by_quarter.items())
    ]


def analyse(data: Dataset, plan: Plan = PLAN) -> dict[str, Any]:
    rows = _rows(data, plan)
    samples = _samples(rows)
    lm = lambda r: r.event.lm_surprise  # noqa: E731
    h2_controls = {"reaction": lambda r: r.reaction, "runup": lambda r: r.runup}

    confirmatory = {
        "h1": {
            name: _regress(sample, lambda r: r.reaction, lm, {}) for name, sample in samples.items()
        },
        "h2": {
            name: _regress(sample, lambda r: r.drift, lm, h2_controls)
            for name, sample in samples.items()
        },
    }
    exploratory_specs: list[tuple[str, Callable[[list[Row]], dict[str, Any] | None]]] = [
        (
            "Drift, 6-factor abnormal returns",
            lambda s: _regress(
                s,
                lambda r: r.ff_drift,
                lm,
                {"reaction": lambda r: r.ff_reaction, "runup": lambda r: r.runup},
            ),
        ),
        (
            "Drift, not winsorised",
            lambda s: _regress(s, lambda r: r.drift, lm, h2_controls, winsorized=False),
        ),
        (
            "Drift, tone level instead of surprise",
            lambda s: _regress(s, lambda r: r.drift, lambda r: r.event.lm.net, h2_controls),
        ),
        (
            "Drift, pipeline lexicon surprise",
            lambda s: _regress(
                s, lambda r: r.drift, lambda r: r.event.lexicon_surprise, h2_controls
            ),
        ),
        (
            "Drift over days +2 to +6",
            lambda s: _regress(s, lambda r: r.drift_short, lm, h2_controls),
        ),
        (
            "Drift over days +2 to +61",
            lambda s: _regress(s, lambda r: r.drift_long, lm, h2_controls),
        ),
        (
            "Reaction, tone level",
            lambda s: _regress(s, lambda r: r.reaction, lambda r: r.event.lm.net, {}),
        ),
        (
            "Reaction, pipeline lexicon surprise",
            lambda s: _regress(s, lambda r: r.reaction, lambda r: r.event.lexicon_surprise, {}),
        ),
    ]
    exploratory = [
        {"label": label, **{name: spec(sample) for name, sample in samples.items()}}
        for label, spec in exploratory_specs
    ]

    primary = _portfolio(data, plan, "lm_surprise", plan.hold)
    periods = _periods(data, plan)
    naive = _portfolio(data, plan, "lm_surprise", plan.hold, naive=True)
    portfolio: dict[str, Any] = {
        "primary": {
            name: _summary(data, primary.net(plan.cost_bps), primary, days, plan)
            for name, days in periods.items()
        },
        "naive": {
            name: _summary(data, naive.net(plan.cost_bps), naive, days, plan)
            for name, days in periods.items()
        },
        "costs": [
            {
                "bps": bps,
                **{
                    k: v
                    for k, v in _summary(
                        data, primary.net(bps), primary, periods["development"], plan
                    ).items()
                    if k in {"sharpe", "ann_return", "alpha_ann", "alpha_t"}
                },
            }
            for bps in plan.costs
        ],
    }
    development = periods["development"]
    variants = []
    trial_sharpes = []
    for signal in ("lm_surprise", "lm_level", "lexicon_surprise"):
        for hold in plan.holds:
            daily = (
                primary
                if (signal, hold) == ("lm_surprise", plan.hold)
                else _portfolio(data, plan, signal, hold)
            )
            net = daily.net(plan.cost_bps)[development.start : development.stop]
            deviation = float(net.std(ddof=1))
            trial_sharpes.append(float(net.mean()) / deviation if deviation > 0 else 0.0)
            variants.append(
                {
                    "signal": signal,
                    "hold": hold,
                    "sharpe": sharpe(net),
                    "ann_return": float(net.mean()) * TRADING_DAYS,
                    "turnover": float(daily.turnover[development.start : development.stop].mean()),
                }
            )
    primary_dev = primary.net(plan.cost_bps)[development.start : development.stop]
    benchmark, probability = deflated_sharpe(primary_dev, trial_sharpes)
    portfolio["variants"] = variants
    portfolio["deflated"] = {
        "trials": len(trial_sharpes),
        "benchmark_annualized": benchmark * math.sqrt(TRADING_DAYS),
        "probability": probability,
    }
    span = range(development.start, periods["holdout"].stop)
    portfolio["equity"] = {
        "dates": [data.calendar.days[i].isoformat() for i in span],
        "holdout_start": plan.holdout_start.isoformat(),
        "honest": np.cumprod(1 + primary.net(plan.cost_bps)[span.start : span.stop]).tolist(),
        "naive": np.cumprod(1 + naive.net(plan.cost_bps)[span.start : span.stop]).tolist(),
    }

    h2_dev, h2_hold = confirmatory["h2"]["development"], confirmatory["h2"]["holdout"]
    dev_port = portfolio["primary"]["development"]
    criteria = [
        {
            "label": "Drift coefficient t > 2 in development",
            "value": None if h2_dev is None else h2_dev["key"]["t"],
            "passed": h2_dev is not None and h2_dev["key"]["t"] > 2,
        },
        {
            "label": "Same sign in the holdout",
            "value": None if h2_hold is None else h2_hold["key"]["coef"],
            "passed": h2_dev is not None
            and h2_hold is not None
            and math.copysign(1, h2_dev["key"]["coef"]) == math.copysign(1, h2_hold["key"]["coef"]),
        },
        {
            "label": "Development net Sharpe interval excludes zero",
            "value": [dev_port["sharpe_ci_low"], dev_port["sharpe_ci_high"]],
            "passed": dev_port["sharpe_ci_low"] > 0 or dev_port["sharpe_ci_high"] < 0,
        },
    ]

    usable = [e for e in data.events if e.firm >= 0]
    sessions: dict[str, int] = defaultdict(int)
    for event in usable:
        sessions[event.session] += 1
    both = [(e.lm.net, e.lexicon.net) for e in data.events if e.lexicon.net is not None]
    tested = [r for r in rows if r.event.lm_surprise is not None]
    funnel = [
        *data.funnel,
        {
            "step": "With tone history (2 earlier releases) and prices, 2016-2025",
            "count": len(tested),
            "note": "",
        },
        {
            "step": "Development sample (2016-2023)",
            "count": sum(not r.holdout for r in tested),
            "note": "",
        },
        {"step": "Holdout (2024-2025)", "count": sum(r.holdout for r in tested), "note": ""},
    ]
    return {
        "kind": "earnings-tone-study",
        "plan": {
            "test_start": plan.test_start.isoformat(),
            "holdout_start": plan.holdout_start.isoformat(),
            "end": plan.end.isoformat(),
            "reaction": plan.reaction,
            "drift": plan.drift,
            "runup": plan.runup,
            "hold": plan.hold,
            "cost_bps": plan.cost_bps,
            "entry_offset": plan.entry_offset,
        },
        "funnel": funnel,
        "rejected": [
            {
                "name": r.name,
                "float_bn": r.float_usd / 1e9,
                "assets_bn": None if r.assets_usd is None else r.assets_usd / 1e9,
                "reason": r.reason,
            }
            for r in data.rejected
        ],
        "unpriced": data.unpriced,
        "timing": {
            "sessions": dict(sorted(sessions.items())),
            "naive_early": sum(e.naive < e.t0 for e in usable),
            "events": len(usable),
        },
        "tone_index": _tone_index(data),
        "tone_agreement": {
            "pearson": float(np.corrcoef(np.array(both).T)[0, 1]) if len(both) > 2 else math.nan,
            "n": len(both),
            "lexicon_silent": sum(
                e.lexicon.positive + e.lexicon.negative == 0 for e in data.events
            ),
            "mean_words": float(np.mean([e.lm.words for e in data.events])),
        },
        "confirmatory": confirmatory,
        "exploratory": exploratory,
        "car_paths": _paths(rows, plan),
        "portfolio": portfolio,
        "decision": {
            "criteria": criteria,
            "worth_more_work": all(c["passed"] for c in criteria),
        },
    }
