"""The point-in-time invariant: at decision time t, every input has available_at <= t.

These tests inject data from the future on purpose and assert that it changes nothing.
If any of them fails, a result produced by this system cannot be trusted.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from alphadots_common.bus import InMemoryBus
from alphadots_contracts import (
    NormalizedEventV1,
    RawNewsV1,
    SentimentScoredV1,
    SignalGeneratedV1,
    Topic,
)
from alphadots_quant import SentimentObservation, decayed_sentiment
from alphadots_signal_engine.processor import SignalConfig, SignalEngine
from alphadots_signal_engine.store import InMemoryScoredEventStore
from scripts.local_stack import LocalStack
from tests.conftest import T0, api_client, make_scored

DAY = 86_400.0


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _all(bus: InMemoryBus, topic: Topic, model: type) -> list:  # type: ignore[type-arg]
    return [model.model_validate_json(record.value) for record in bus.records(topic.value)]


# -------------------------------------------------------------------- quant


def _obs(event_id: str, offset_seconds: float, signed: float) -> SentimentObservation:
    return SentimentObservation(
        event_id=event_id,
        available_at=T0 + timedelta(seconds=offset_seconds),
        signed_sentiment=signed,
        relevance=1.0,
        materiality=1.0,
    )


def test_an_observation_available_after_the_decision_time_is_excluded() -> None:
    past = [_obs("a", -3600, 0.4), _obs("b", -60, -0.2)]
    future = [_obs("leak-1", 1e-3, 1.0), _obs("leak-2", 3600, -1.0), _obs("leak-3", 30 * DAY, 1.0)]

    clean = decayed_sentiment(past, as_of=T0, tau_seconds=DAY, lookback_seconds=5 * DAY)
    polluted = decayed_sentiment(past + future, as_of=T0, tau_seconds=DAY, lookback_seconds=5 * DAY)

    assert polluted.excluded_future == 3
    assert polluted.contributions == clean.contributions
    assert (polluted.score, polluted.score_normalized, polluted.effective_weight) == (
        clean.score,
        clean.score_normalized,
        clean.effective_weight,
    )


def test_an_observation_exactly_at_the_decision_time_is_included() -> None:
    result = decayed_sentiment(
        [_obs("now", 0, 0.5)], as_of=T0, tau_seconds=DAY, lookback_seconds=DAY
    )
    assert [c.event_id for c in result.contributions] == ["now"]
    assert result.excluded_future == 0


def test_future_data_never_changes_a_randomised_aggregate() -> None:
    rng = random.Random(2026)
    for _ in range(200):
        past = [_obs(f"p{i}", -rng.uniform(0, 4 * DAY), rng.uniform(-1, 1)) for i in range(20)]
        future = [_obs(f"f{i}", rng.uniform(1e-6, 4 * DAY), rng.uniform(-1, 1)) for i in range(20)]
        mixed = past + future
        rng.shuffle(mixed)
        clean = decayed_sentiment(past, as_of=T0, tau_seconds=DAY, lookback_seconds=5 * DAY)
        polluted = decayed_sentiment(mixed, as_of=T0, tau_seconds=DAY, lookback_seconds=5 * DAY)
        assert polluted.score == clean.score
        assert polluted.excluded_future == 20


# ------------------------------------------------------------ signal engine


def _engine() -> SignalEngine:
    return SignalEngine(
        store=InMemoryScoredEventStore(),
        config=SignalConfig(
            tau_seconds=DAY,
            lookback_seconds=5 * DAY,
            stance_threshold=0.15,
            min_effective_weight=0.2,
            stage_allowance=timedelta(seconds=1),
        ),
    )


async def _emit_all(engine: SignalEngine, **kwargs: object) -> dict[str, SignalGeneratedV1]:
    """Every signal the event caused, keyed by the event that triggers each one."""
    outputs = await engine.handle(make_scored(**kwargs))  # type: ignore[arg-type]
    signals: dict[str, SignalGeneratedV1] = {}
    for outbound in outputs:
        assert isinstance(outbound.message, SignalGeneratedV1)
        signals[outbound.message.triggering_event_id] = outbound.message
    return signals


async def _emit(engine: SignalEngine, **kwargs: object) -> SignalGeneratedV1:
    """The signal as of this event's own availability time."""
    return (await _emit_all(engine, **kwargs))[make_scored(**kwargs).event_id]  # type: ignore[arg-type]


async def test_a_late_arriving_older_event_cannot_see_newer_events() -> None:
    """Out-of-order delivery: B (later in event time) is processed before A."""
    engine = _engine()
    b_time = T0 + timedelta(hours=1)
    provisional_b = await _emit(engine, event_key="B", available_at=b_time, signed_sentiment=-0.9)
    assert provisional_b.coverage.events_used == 1, "A has not been processed yet"

    caused = await _emit_all(engine, event_key="A", available_at=T0, signed_sentiment=0.6)
    a_id, b_id = make_scored(event_key="A").event_id, make_scored(event_key="B").event_id
    assert set(caused) == {a_id, b_id}, "A's own signal, plus a restatement of B's"

    earlier = caused[a_id]
    assert earlier.as_of == T0
    assert [c.scored_event_id for c in earlier.contributions] == [a_id]
    assert earlier.score_normalized == pytest.approx(0.6), "B is in the store but in A's future"

    restated_b = caused[b_id]
    assert restated_b.event_id == provisional_b.event_id, "same ID, so it replaces downstream"
    assert restated_b.as_of == b_time
    assert [c.scored_event_id for c in restated_b.contributions] == [a_id, b_id]
    assert all(c.available_at <= restated_b.as_of for c in restated_b.contributions)


async def test_signals_do_not_depend_on_the_order_events_were_processed_in() -> None:
    """Shuffle arrival order: the final signal for every event must come out identical."""
    rng = random.Random(99)
    events = [
        {
            "event_key": f"e{index:02d}",
            "available_at": T0 + timedelta(seconds=rng.uniform(0, 6 * DAY)),
            "signed_sentiment": round(rng.uniform(-1, 1), 3),
            "abstained": rng.random() < 0.15,
            "novelty": 0.0 if rng.random() < 0.1 else 1.0,
            "source": rng.choice(("wire_a", "wire_b")),
        }
        for index in range(40)
    ]

    async def final_signals(order: list[dict[str, object]]) -> dict[str, str]:
        engine = _engine()
        latest: dict[str, str] = {}
        for kwargs in order:
            for trigger_id, signal in (await _emit_all(engine, **kwargs)).items():
                latest[trigger_id] = signal.model_dump_json()
        return latest

    in_order = await final_signals(sorted(events, key=lambda e: e["available_at"]))  # type: ignore[arg-type, return-value]
    assert len(in_order) == len(events)
    for _ in range(5):
        shuffled = list(events)
        rng.shuffle(shuffled)
        assert await final_signals(shuffled) == in_order  # type: ignore[arg-type]
    assert await final_signals(list(reversed(events))) == in_order  # type: ignore[arg-type]


async def test_a_post_decision_document_in_the_store_does_not_change_the_signal() -> None:
    clean_engine, polluted_engine = _engine(), _engine()
    for engine in (clean_engine, polluted_engine):
        await _emit(engine, event_key="history", available_at=T0 - timedelta(hours=3))
    # A document from one second after the decision time, already sitting in the store.
    await _emit(
        polluted_engine,
        event_key="leak",
        available_at=T0 + timedelta(seconds=1),
        signed_sentiment=-1.0,
    )

    clean = await _emit(clean_engine, event_key="decision", available_at=T0)
    polluted = await _emit(polluted_engine, event_key="decision", available_at=T0)

    assert polluted == clean
    assert all(c.available_at <= polluted.as_of for c in polluted.contributions)


# ---------------------------------------------------------------- pipeline


async def test_availability_only_moves_forward_through_the_pipeline(loaded: LocalStack) -> None:
    raw = {m.event_id: m for m in _all(loaded.bus, Topic.NEWS_RAW, RawNewsV1)}
    normalized = {
        m.event_id: m for m in _all(loaded.bus, Topic.EVENTS_NORMALIZED, NormalizedEventV1)
    }
    scored = {m.event_id: m for m in _all(loaded.bus, Topic.SENTIMENT_SCORED, SentimentScoredV1)}
    signals = _all(loaded.bus, Topic.SIGNALS_GENERATED, SignalGeneratedV1)
    assert len(raw) == len(normalized) == len(scored) == 137
    assert signals

    for event in normalized.values():
        source = raw[event.raw_event_id]
        assert event.available_at > source.available_at >= source.first_seen_at
    for score in scored.values():
        assert score.available_at > normalized[score.normalized_event_id].available_at
    for signal in signals:
        trigger = scored[signal.triggering_event_id]
        assert signal.as_of == trigger.available_at
        assert signal.available_at > signal.as_of
        for contribution in signal.contributions:
            assert contribution.available_at <= signal.as_of
            assert scored[contribution.scored_event_id].available_at == contribution.available_at


async def test_no_signal_uses_evidence_first_seen_after_its_decision_time(
    loaded: LocalStack,
) -> None:
    """Late arrivals: an item counts from when it was seen, never from when it was published."""
    scored = {m.event_id: m for m in _all(loaded.bus, Topic.SENTIMENT_SCORED, SentimentScoredV1)}
    late = [
        m for m in scored.values() if (m.first_seen_at - m.published_at) > timedelta(minutes=30)
    ]
    assert late, "the fixtures must contain late arrivals for this test to mean anything"

    for signal in _all(loaded.bus, Topic.SIGNALS_GENERATED, SignalGeneratedV1):
        for contribution in signal.contributions:
            evidence = scored[contribution.scored_event_id]
            assert evidence.first_seen_at < signal.as_of
    for item in late:
        assert item.available_at > item.first_seen_at > item.published_at


# --------------------------------------------------------------------- API


async def test_api_never_returns_anything_from_after_as_of(loaded: LocalStack) -> None:
    async with api_client(loaded) as client:
        feed = (await client.get("/v1/overview")).json()["feed"]
        start, end = _parse(feed["earliest_available_at"]), _parse(feed["latest_available_at"])
        rng = random.Random(5)
        cuts = [start + (end - start) * rng.random() for _ in range(12)]

        for cut in cuts:
            as_of = _iso(cut)
            bound = _parse(as_of)
            events = (await client.get("/v1/events", params={"as_of": as_of, "limit": 200})).json()
            assert events["data"], as_of
            for event in events["data"]:
                assert _parse(event["available_at"]) <= bound
                if event["sentiment"] is not None:
                    assert _parse(event["sentiment"]["available_at"]) <= bound

            for symbol in ("NWSC", "CBHB"):
                signals = (
                    await client.get(f"/v1/signals/{symbol}", params={"as_of": as_of})
                ).json()
                for point in signals["history"]:
                    assert _parse(point["available_at"]) <= bound
                if signals["latest"] is not None:
                    assert _parse(signals["latest"]["available_at"]) <= bound
                    for contribution in signals["latest"]["contributions"]:
                        assert _parse(contribution["available_at"]) <= bound

                sentiment = (
                    await client.get(
                        f"/v1/sentiment/{symbol}", params={"as_of": as_of, "window": "7d"}
                    )
                ).json()
                for point in sentiment["points"]:
                    assert _parse(point["available_at"]) <= bound


async def test_a_score_is_hidden_until_it_becomes_available(seeded: LocalStack) -> None:
    async with api_client(seeded) as client:
        newest = (await client.get("/v1/events", params={"limit": 1})).json()["data"][0]
        event_time = newest["available_at"]
        score_time = newest["sentiment"]["available_at"]
        assert _parse(score_time) > _parse(event_time)

        at_event = (await client.get("/v1/events", params={"as_of": event_time, "limit": 1})).json()
        assert at_event["data"][0]["event_id"] == newest["event_id"]
        assert at_event["data"][0]["sentiment"] is None, (
            "the event is visible; its score is not yet"
        )

        at_score = (await client.get("/v1/events", params={"as_of": score_time, "limit": 1})).json()
        assert (
            at_score["data"][0]["sentiment"]["scored_event_id"]
            == newest["sentiment"]["scored_event_id"]
        )

        detail = await client.get(
            f"/v1/events/{newest['event_id']}",
            params={"as_of": _iso(_parse(event_time) - timedelta(seconds=1))},
        )
        assert detail.status_code == 404, "an event does not exist before it was available"


async def test_nothing_is_visible_before_the_first_event(seeded: LocalStack) -> None:
    async with api_client(seeded) as client:
        as_of = "2026-03-02T13:30:05Z"  # one second before the first normalised event
        overview = (await client.get("/v1/overview", params={"as_of": as_of})).json()
        counts = dict(overview["pipeline"])
        assert counts.pop("dead_letters") == 2, "operational records are not time-filtered"
        assert set(counts.values()) == {0}
        assert all(item["latest_signal"] is None for item in overview["instruments"])
        assert (await client.get("/v1/events", params={"as_of": as_of})).json()["data"] == []
        signals = (await client.get("/v1/signals/NWSC", params={"as_of": as_of})).json()
        assert (signals["latest"], signals["history"]) == (None, [])


async def test_visible_counts_never_decrease_as_as_of_advances(loaded: LocalStack) -> None:
    async with api_client(loaded) as client:
        previous: dict[str, int] | None = None
        for day in range(2, 15):
            as_of = f"2026-03-{day:02d}T23:59:59Z"
            counts = (await client.get("/v1/overview", params={"as_of": as_of})).json()["pipeline"]
            if previous is not None:
                assert all(counts[key] >= previous[key] for key in counts), as_of
            previous = counts
        latest = (await client.get("/v1/overview")).json()["pipeline"]
        assert previous == latest, "by the end of the window as_of shows everything"
