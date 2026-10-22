"""Decay aggregate maths and the heuristic signal built on it."""

from __future__ import annotations

import math
import random
import re
from datetime import datetime, timedelta

import pytest

from alphadots_common.db import make_engine
from alphadots_contracts import (
    InstrumentRef,
    RiskFlag,
    SignalAbstainReason,
    SignalGeneratedV1,
    Stance,
    ValidationStatus,
)
from alphadots_quant import SentimentObservation, decayed_sentiment
from alphadots_signal_engine.processor import MAX_CONTRIBUTIONS, SignalConfig, SignalEngine
from alphadots_signal_engine.store import (
    InMemoryScoredEventStore,
    ScoredEventStore,
    ScoredRow,
    SqlScoredEventStore,
)
from tests.conftest import T0, make_scored

HOUR = 3600.0
DAY = 86_400.0
NWSC = InstrumentRef(symbol="NWSC", instrument_id="SYN-0001", confidence=1.0)


def _obs(
    event_id: str,
    age_seconds: float,
    signed: float,
    relevance: float = 1.0,
    materiality: float = 1.0,
) -> SentimentObservation:
    return SentimentObservation(
        event_id=event_id,
        available_at=T0 - timedelta(seconds=age_seconds),
        signed_sentiment=signed,
        relevance=relevance,
        materiality=materiality,
    )


# -------------------------------------------------------------------- quant


def test_decay_matches_the_formula_by_hand() -> None:
    observations = [_obs("a", 0, 0.8), _obs("b", DAY, -0.5, relevance=0.5, materiality=0.8)]
    result = decayed_sentiment(observations, as_of=T0, tau_seconds=DAY, lookback_seconds=5 * DAY)

    weight_b = 0.5 * 0.8 * math.exp(-1.0)
    assert result.score == pytest.approx(0.8 + weight_b * -0.5)
    assert result.effective_weight == pytest.approx(1.0 + weight_b)
    assert result.score_normalized == pytest.approx(result.score / result.effective_weight)
    assert [c.event_id for c in result.contributions] == ["b", "a"], "oldest first"


def test_half_life_behaviour() -> None:
    half_life = DAY * math.log(2)
    result = decayed_sentiment(
        [_obs("a", half_life, 1.0)], as_of=T0, tau_seconds=DAY, lookback_seconds=5 * DAY
    )
    assert result.score == pytest.approx(0.5)
    assert result.score_normalized == pytest.approx(1.0), "normalising removes the decay level"


def test_empty_input_is_a_zero_not_an_error() -> None:
    result = decayed_sentiment([], as_of=T0, tau_seconds=DAY, lookback_seconds=DAY)
    assert (result.score, result.score_normalized, result.effective_weight) == (0.0, 0.0, 0.0)
    assert result.contributions == ()


def test_stale_observations_are_dropped_at_the_lookback_boundary() -> None:
    result = decayed_sentiment(
        [_obs("edge", DAY, 1.0), _obs("stale", DAY + 1, 1.0)],
        as_of=T0,
        tau_seconds=DAY,
        lookback_seconds=DAY,
    )
    assert [c.event_id for c in result.contributions] == ["edge"]
    assert result.excluded_stale == 1


def test_result_does_not_depend_on_input_order() -> None:
    rng = random.Random(11)
    observations = [
        _obs(f"e{i}", rng.uniform(0, 4 * DAY), rng.uniform(-1, 1), rng.random(), rng.random())
        for i in range(200)
    ]
    baseline = decayed_sentiment(observations, as_of=T0, tau_seconds=DAY, lookback_seconds=5 * DAY)
    for _ in range(5):
        rng.shuffle(observations)
        shuffled = decayed_sentiment(
            observations, as_of=T0, tau_seconds=DAY, lookback_seconds=5 * DAY
        )
        assert shuffled == baseline, "bit-identical, so replays cannot drift"
    assert -1.0 <= baseline.score_normalized <= 1.0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"tau_seconds": 0, "lookback_seconds": DAY}, "tau_seconds"),
        ({"tau_seconds": DAY, "lookback_seconds": 0}, "lookback_seconds"),
    ],
)
def test_invalid_parameters_are_rejected(kwargs: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        decayed_sentiment([], as_of=T0, **kwargs)


def test_naive_times_are_rejected() -> None:
    naive = datetime(2026, 3, 2, 14, 0)  # noqa: DTZ001 - the naive value is the point
    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        decayed_sentiment([], as_of=naive, tau_seconds=DAY, lookback_seconds=DAY)
    bad = SentimentObservation("a", naive, 0.5, 1.0, 1.0)
    with pytest.raises(ValueError, match=re.escape("observation.available_at")):
        decayed_sentiment([bad], as_of=T0, tau_seconds=DAY, lookback_seconds=DAY)


# -------------------------------------------------------------------- store


@pytest.fixture(params=["memory", "sql"])
async def store(request: pytest.FixtureRequest) -> ScoredEventStore:
    if request.param == "memory":
        return InMemoryScoredEventStore()
    sql = SqlScoredEventStore(make_engine("sqlite+aiosqlite://"))
    await sql.create_schema()
    return sql


def _row(event_id: str, at: datetime, instrument_id: str = "SYN-0001") -> ScoredRow:
    return ScoredRow(
        scored_event_id=event_id,
        instrument_id=instrument_id,
        symbol="NWSC",
        normalized_event_id=f"norm_{event_id}",
        trace_id="0" * 32,
        source="w",
        available_at=at,
        signed_sentiment=0.5,
        relevance=1.0,
        materiality=1.0,
        abstained=False,
        is_duplicate=False,
    )


async def test_store_range_is_inclusive_bounded_and_idempotent(store: ScoredEventStore) -> None:
    rows = [
        _row("before", T0 - timedelta(hours=2)),
        _row("start", T0 - timedelta(hours=1)),
        _row("end", T0),
        _row("after", T0 + timedelta(seconds=1)),
        _row("other", T0, instrument_id="SYN-0002"),
    ]
    await store.put(rows)
    await store.put(rows)  # redelivery

    window = await store.between("SYN-0001", start=T0 - timedelta(hours=1), end=T0)
    assert sorted(row.scored_event_id for row in window) == ["end", "start"]
    assert all(row.available_at.utcoffset() == timedelta(0) for row in window)


# ------------------------------------------------------------------- engine


def _config(**overrides: float) -> SignalConfig:
    values: dict[str, float] = {
        "tau_seconds": DAY,
        "lookback_seconds": 5 * DAY,
        "stance_threshold": 0.15,
        "min_effective_weight": 0.2,
    }
    values.update(overrides)
    return SignalConfig(stage_allowance=timedelta(seconds=1), **values)


@pytest.fixture
def engine() -> SignalEngine:
    return SignalEngine(store=InMemoryScoredEventStore(), config=_config())


async def _signals(engine: SignalEngine, **scored_kwargs: object) -> list[SignalGeneratedV1]:
    outputs = await engine.handle(make_scored(**scored_kwargs))  # type: ignore[arg-type]
    signals = [outbound.message for outbound in outputs]
    assert all(isinstance(signal, SignalGeneratedV1) for signal in signals)
    return signals  # type: ignore[return-value]


async def _signal(engine: SignalEngine, **scored_kwargs: object) -> SignalGeneratedV1:
    """The signal triggered by this event itself (restatements of later ones are ignored)."""
    own_id = make_scored(**scored_kwargs).event_id  # type: ignore[arg-type]
    (own,) = [s for s in await _signals(engine, **scored_kwargs) if s.triggering_event_id == own_id]
    return own


async def test_signal_carries_its_research_only_provenance(engine: SignalEngine) -> None:
    signal = await _signal(engine, signed_sentiment=0.6)

    assert signal.validation_status is ValidationStatus.UNVALIDATED_HEURISTIC
    assert signal.research_only is True
    assert signal.params.tau_source == "config_prior_unestimated"
    assert signal.as_of == T0
    assert signal.available_at == T0 + timedelta(seconds=1)
    assert (signal.score, signal.score_normalized, signal.stance) == (
        pytest.approx(0.6),
        pytest.approx(0.6),
        Stance.POSITIVE,
    )
    assert [c.scored_event_id for c in signal.contributions] == [signal.triggering_event_id]
    assert signal.risk_flags == (RiskFlag.LOW_COVERAGE, RiskFlag.SINGLE_SOURCE)


@pytest.mark.parametrize(
    ("signed", "stance"),
    [
        (0.15, Stance.POSITIVE),
        (0.149, Stance.NEUTRAL),
        (-0.149, Stance.NEUTRAL),
        (-0.15, Stance.NEGATIVE),
    ],
)
async def test_stance_threshold(engine: SignalEngine, signed: float, stance: Stance) -> None:
    assert (await _signal(engine, signed_sentiment=signed)).stance is stance


async def test_weak_evidence_abstains_and_stays_neutral(engine: SignalEngine) -> None:
    weak = (InstrumentRef(symbol="NWSC", instrument_id="SYN-0001", confidence=0.25),)
    signal = await _signal(engine, signed_sentiment=0.9, instruments=weak, materiality=0.3)

    assert signal.abstained
    assert signal.abstain_reason is SignalAbstainReason.INSUFFICIENT_EVIDENCE
    assert signal.stance is Stance.NEUTRAL
    assert signal.coverage.effective_weight == pytest.approx(0.075)
    assert RiskFlag.AMBIGUOUS_ISSUER in signal.risk_flags
    assert signal.score_normalized == pytest.approx(0.9), "the score is reported, just not acted on"


async def test_abstained_and_duplicate_events_do_not_move_the_score(engine: SignalEngine) -> None:
    await _signal(engine, event_key="a", signed_sentiment=0.6)
    after_abstain = await _signal(
        engine, event_key="b", abstained=True, available_at=T0 + timedelta(minutes=1)
    )
    after_copy = await _signal(
        engine,
        event_key="c",
        signed_sentiment=-0.9,
        novelty=0.0,
        available_at=T0 + timedelta(minutes=2),
    )

    assert after_copy.score_normalized == pytest.approx(0.6)
    coverage = after_copy.coverage
    assert (
        coverage.events_considered,
        coverage.events_used,
        coverage.events_abstained,
        coverage.duplicates_excluded,
    ) == (3, 1, 1, 1)
    assert after_abstain.coverage.events_used == 1


async def test_only_unusable_events_means_no_usable_events(engine: SignalEngine) -> None:
    signal = await _signal(engine, abstained=True)
    assert signal.abstain_reason is SignalAbstainReason.NO_USABLE_EVENTS
    assert (signal.score, signal.contributions, signal.risk_flags) == (0.0, (), ())


async def test_unresolved_event_produces_no_signal(engine: SignalEngine) -> None:
    assert await engine.handle(make_scored(instruments=())) == []


async def test_multi_issuer_event_produces_one_signal_per_instrument(engine: SignalEngine) -> None:
    refs = (NWSC, InstrumentRef(symbol="APXR", instrument_id="SYN-0004", confidence=0.98))
    outputs = await engine.handle(make_scored(instruments=refs))
    assert [o.key for o in outputs] == ["SYN-0001", "SYN-0004"], "keyed by instrument"
    assert outputs[0].message.event_id != outputs[1].message.event_id


async def test_two_sources_clear_the_single_source_flag(engine: SignalEngine) -> None:
    await _signal(engine, event_key="a", source="wire_a")
    signal = await _signal(
        engine, event_key="b", source="wire_b", available_at=T0 + timedelta(minutes=1)
    )
    assert signal.risk_flags == ()


async def test_redelivery_reproduces_the_identical_signal(engine: SignalEngine) -> None:
    await _signal(engine, event_key="a", signed_sentiment=0.4)
    first = await _signal(engine, event_key="b", available_at=T0 + timedelta(hours=1))
    again = await _signal(engine, event_key="b", available_at=T0 + timedelta(hours=1))
    assert again == first
    assert again.model_dump_json() == first.model_dump_json()


async def test_contributions_are_truncated_to_the_largest(engine: SignalEngine) -> None:
    total = MAX_CONTRIBUTIONS + 5
    signal = None
    for index in range(total):
        signal = await _signal(
            engine,
            event_key=f"e{index:02d}",
            signed_sentiment=0.5,
            available_at=T0 + timedelta(seconds=index * HOUR / 10),
        )
    assert signal is not None
    assert signal.coverage.events_used == total
    assert len(signal.contributions) == MAX_CONTRIBUTIONS
    assert signal.contributions_truncated
    kept = [c.available_at for c in signal.contributions]
    assert kept == sorted(kept)
    assert min(kept) > T0, "the oldest, most decayed contributions are the ones dropped"
