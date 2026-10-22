"""The vertical slice end to end: fixture -> ingest -> normalise -> score -> signal -> API.

Runs the real service code on the in-memory bus with SQLite. Broker and PostgreSQL
behaviour is covered separately by ``tests/integration``.
"""

from __future__ import annotations

import contextlib
from typing import Any

import httpx

from alphadots_common.bus import Record
from alphadots_contracts import RawNewsV1, Topic
from scripts.local_stack import LocalStack
from tests.conftest import REPLAY, SEED, api_client, make_settings, snapshot_tables

DERIVED_TOPICS = (Topic.EVENTS_NORMALIZED, Topic.SENTIMENT_SCORED, Topic.SIGNALS_GENERATED)


async def snapshot(stack: LocalStack) -> dict[str, list[tuple[Any, ...]]]:
    return await snapshot_tables(stack.engine)


async def _by_source_id(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    events = (await client.get("/v1/events", params={"limit": 200})).json()["data"]
    return {event["source_event_id"]: event for event in events}


def _values(stack: LocalStack, topic: Topic) -> list[bytes]:
    return [record.value for record in stack.bus.records(topic.value)]


# ----------------------------------------------------------- the vertical slice


async def test_a_synthetic_headline_reaches_the_api_with_provenance(seeded: LocalStack) -> None:
    async with api_client(seeded) as client:
        event = (await _by_source_id(client))["sw-a-0001"]

    assert (
        event["headline"] == "Northwind Semiconductor beats estimates as data-center revenue grows"
    )
    assert event["instruments"] == [
        {"symbol": "NWSC", "instrument_id": "SYN-0001", "confidence": 0.98}
    ]
    assert event["source"] == "synthetic_wire_a"
    assert event["source_url"] == "https://wire-a.example.invalid/news/sw-a-0001"
    assert event["source_license"] == "CC0-1.0-synthetic"
    assert event["payload_ref"] == "fixture://news/seed.jsonl#L4"
    assert (
        event["published_at"],
        event["first_seen_at"],
        event["ingested_at"],
        event["available_at"],
        event["sentiment"]["available_at"],
    ) == (
        "2026-03-02T13:30:00Z",
        "2026-03-02T13:30:04Z",
        "2026-03-02T13:30:05Z",
        "2026-03-02T13:30:06Z",
        "2026-03-02T13:30:07Z",
    )
    assert event["source_latency_seconds"] == 4.0
    assert event["sentiment"]["model_id"] == "lexicon-baseline"
    assert event["sentiment"]["calibration_version"] == "uncalibrated"
    assert event["sentiment"]["rationale_terms"] == ["+beats estimates", "+grows", "+raised"]
    assert len(event["trace_id"]) == 32


async def test_trace_id_is_carried_from_ingest_to_signal(seeded: LocalStack) -> None:
    def trace_ids(topic: Topic) -> set[str]:
        return {record.headers["trace_id"] for record in seeded.bus.records(topic.value)}

    raw = trace_ids(Topic.NEWS_RAW)
    assert len(raw) == 16
    assert trace_ids(Topic.EVENTS_NORMALIZED) == raw
    assert trace_ids(Topic.SENTIMENT_SCORED) == raw
    assert trace_ids(Topic.SIGNALS_GENERATED) <= raw


async def test_each_curated_edge_case_behaves_as_documented(seeded: LocalStack) -> None:
    async with api_client(seeded) as client:
        events = await _by_source_id(client)
        apex_signals = {
            symbol: (
                await client.get(f"/v1/signals/{symbol}", params={"as_of": "2026-03-02T15:00:00Z"})
            ).json()
            for symbol in ("APXR", "APXE")
        }

    copy = events["sw-b-0001"]
    assert copy["duplicate_of"] == events["sw-a-0001"]["event_id"]
    assert copy["sentiment"]["novelty"] == 0.0
    assert copy["headline"].startswith("NORTHWIND SEMICONDUCTOR beats"), "kept as received"

    negated = events["sw-b-0002"]
    assert negated["sentiment"]["rationale_terms"] == ["+cut"], "'did not cut' reads as positive"
    assert negated["instruments"][0]["confidence"] == 0.75, "name-only link"

    ambiguous = events["sw-a-0003"]
    assert ambiguous["resolution_flags"] == ["ambiguous_issuer"]
    assert {ref["symbol"]: ref["confidence"] for ref in ambiguous["instruments"]} == {
        "APXR": 0.25,
        "APXE": 0.25,
    }
    for body in apex_signals.values():
        assert body["latest"]["abstained"]
        assert body["latest"]["abstain_reason"] == "insufficient_evidence"
        assert "ambiguous_issuer" in body["latest"]["risk_flags"]

    unresolved = events["sw-b-0003"]
    assert (unresolved["instruments"], unresolved["resolution_flags"]) == ([], ["unresolved"])

    assert events["sw-a-0005"]["resolution_flags"] == ["provider_symbol_unknown"]
    assert events["sw-b-0004"]["sentiment"]["abstain_reason"] == "unsupported_language"
    assert events["sw-a-0006"]["sentiment"]["abstain_reason"] == "no_lexicon_match"
    assert events["sw-a-0007"]["sentiment"]["abstain_reason"] == "high_entropy"
    assert events["sw-a-0008"]["resolution_flags"] == ["multi_issuer"]

    late = events["sw-b-0006"]
    assert late["source_latency_seconds"] == 17_100.0
    assert late["available_at"] == "2026-03-02T19:45:02Z", "usable from when seen, not published"

    assert events["sw-a-0009"]["instruments"][0]["symbol"] == "VRCS"
    stale_tag = events["sw-b-0007"]
    assert stale_tag["resolution_flags"] == ["provider_symbol_unknown"]
    assert stale_tag["instruments"] == [
        {"symbol": "VRCS", "instrument_id": "SYN-0008", "confidence": 0.75}
    ]


async def test_hostile_markup_never_reaches_the_read_model(seeded: LocalStack) -> None:
    async with api_client(seeded) as client:
        event = (await _by_source_id(client))["sw-b-0005"]
    text = f"{event['headline']} {event['body']}"
    assert event["headline"] == "Vireo Cloud Software posts record revenue"
    for fragment in ("<", ">", "script", "onerror", "alert("):
        assert fragment not in text
    # Cleaning removes markup, not meaning: the injected sentence is still there as plain
    # text, and still fools a word-counting baseline. The research agent must treat
    # evidence as untrusted regardless (M4).
    assert "Ignore all previous instructions" in event["body"]
    assert "+strong" in event["sentiment"]["rationale_terms"]


async def test_poison_source_lines_are_quarantined_and_the_stream_continues(
    seeded: LocalStack,
) -> None:
    async with api_client(seeded) as client:
        dead = (await client.get("/v1/dead-letters")).json()["data"]
        events = await _by_source_id(client)
    assert sorted(item["original_key"] for item in dead) == [
        "fixture://news/seed.jsonl#L18",
        "fixture://news/seed.jsonl#L19",
    ]
    assert "sw-a-0009" in events, "records after the poison lines were still processed"


# ----------------------------------------------- idempotency and determinism


async def test_replaying_a_fixture_twice_changes_nothing(stack: LocalStack) -> None:
    await stack.ingest(SEED, REPLAY)
    await stack.drain()
    before = await snapshot(stack)

    await stack.ingest(SEED, REPLAY)
    assert await stack.drain() > 0, "the duplicates really were redelivered and reprocessed"

    assert len(stack.bus.records(Topic.NEWS_RAW.value)) == 2 * 137
    assert await snapshot(stack) == before, "no duplicate events, scores or signals"

    for topic in (Topic.EVENTS_NORMALIZED, Topic.SENTIMENT_SCORED):
        values = _values(stack, topic)
        assert values[:137] == values[137:], f"{topic} re-emitted byte-identical messages"


async def test_arrival_order_does_not_change_the_result(loaded: LocalStack) -> None:
    """``loaded`` ingests the seed file and then the replay file, so the seed's 9-10 March
    items are processed before the replay's 3-8 March items. A single stream merged into
    arrival order must end in exactly the same state."""
    in_order = await LocalStack.create(make_settings())
    try:
        await in_order.ingest(SEED, REPLAY)
        await in_order.drain()

        first_seen = [
            RawNewsV1.model_validate_json(value).first_seen_at
            for value in _values(in_order, Topic.NEWS_RAW)
        ]
        assert first_seen == sorted(first_seen), "the merged stream really is in arrival order"
        out_of_order = [
            RawNewsV1.model_validate_json(value).first_seen_at
            for value in _values(loaded, Topic.NEWS_RAW)
        ]
        assert out_of_order != sorted(out_of_order), "and the sequential one really is not"

        assert await snapshot(in_order) == await snapshot(loaded)
    finally:
        await in_order.close()


async def test_restart_mid_stream_gives_the_same_result_as_an_uninterrupted_run(
    loaded: LocalStack,
) -> None:
    interrupted = await LocalStack.create(make_settings())
    try:
        await interrupted.ingest(SEED)
        # Each stage handles a few records, then every worker is torn down and rebuilt.
        for worker in interrupted.workers:
            await worker.start()
            consumer = worker._require_consumer()
            for _ in range(3):
                record = await consumer.poll(0)
                if record is not None:
                    await worker.process(record)
        await interrupted.restart_workers()
        await interrupted.ingest(REPLAY)
        await interrupted.drain()
        await interrupted.restart_workers()
        assert await interrupted.drain() == 0, "nothing is redelivered after a clean commit"

        assert await snapshot(interrupted) == await snapshot(loaded)
    finally:
        await interrupted.close()


async def test_crash_before_commit_redelivers_without_duplicating_effects(
    seeded: LocalStack,
) -> None:
    """Every stage loses its commit for one record, so each record is processed twice."""
    crashed = await LocalStack.create(make_settings())
    try:
        await crashed.ingest(SEED)
        for _ in range(4):  # let each stage's replayed output reach the next stage
            for worker in crashed.workers:
                await worker.start()
                consumer = worker._require_consumer()
                real_commit = consumer.commit

                async def lost_commit(_record: Record) -> None:
                    raise ConnectionError("process died before the offset was committed")

                consumer.commit = lost_commit  # type: ignore[method-assign]
                record = await consumer.poll(0)
                if record is not None:
                    with contextlib.suppress(ConnectionError):
                        await worker.process(record)
                consumer.commit = real_commit  # type: ignore[method-assign]
            await crashed.restart_workers()
        await crashed.drain()

        assert len(crashed.bus.records(Topic.EVENTS_NORMALIZED.value)) > 16, (
            "the uncommitted record was reprocessed and republished"
        )
        assert await snapshot(crashed) == await snapshot(seeded)
    finally:
        await crashed.close()


async def test_two_independent_runs_produce_byte_identical_streams(loaded: LocalStack) -> None:
    other = await LocalStack.create(make_settings())
    try:
        await other.ingest(SEED)
        await other.drain()
        await other.ingest(REPLAY)
        await other.drain()
        for topic in (Topic.NEWS_RAW, *DERIVED_TOPICS):
            assert _values(other, topic) == _values(loaded, topic), topic
    finally:
        await other.close()


async def test_signal_parameters_come_from_configuration(seeded: LocalStack) -> None:
    fast_decay = await LocalStack.create(make_settings(signal_tau_seconds=600.0))
    try:
        await fast_decay.ingest(SEED)
        await fast_decay.drain()
        async with api_client(fast_decay) as fast, api_client(seeded) as default:
            quick = (await fast.get("/v1/signals/NWSC")).json()["latest"]
            slow = (await default.get("/v1/signals/NWSC")).json()["latest"]
        assert quick["params"]["tau_seconds"] == 600.0
        assert slow["params"]["tau_seconds"] == 86_400.0
        assert quick["coverage"]["effective_weight"] < slow["coverage"]["effective_weight"]
    finally:
        await fast_decay.close()
