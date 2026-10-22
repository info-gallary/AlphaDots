"""Delivery semantics: offsets, retries, quarantine and crash-redelivery."""

from __future__ import annotations

import json
import random
from collections.abc import Sequence

import pytest

from alphadots_common.bus import InMemoryBus, Record
from alphadots_common.worker import (
    Outbound,
    RejectMessage,
    RetryPolicy,
    Route,
    StreamWorker,
    encode_message,
)
from alphadots_contracts import DeadLetterReason, DeadLetterV1, RawNewsV1, Topic
from tests.conftest import make_raw

GROUP = "test.group"


class Recorder:
    """A handler that records what it saw and fails on demand."""

    def __init__(self, failures: Sequence[Exception] = ()) -> None:
        self.seen: list[str] = []
        self._failures = list(failures)

    async def __call__(self, message: RawNewsV1) -> Sequence[Outbound]:
        self.seen.append(message.event_id)
        if self._failures:
            raise self._failures.pop(0)
        return [Outbound(Topic.EVENTS_NORMALIZED, message.event_id, message)]


async def _no_sleep(_seconds: float) -> None:
    return None


def _worker(bus: InMemoryBus, handler: Recorder, **kwargs: object) -> StreamWorker:
    return StreamWorker(
        service="test",
        bus=bus,
        group=GROUP,
        routes={Topic.NEWS_RAW: Route(RawNewsV1, handler)},
        retry=RetryPolicy(max_attempts=3, base_delay_seconds=0.0),
        sleep=_no_sleep,
        **kwargs,  # type: ignore[arg-type]
    )


async def _send(bus: InMemoryBus, message: RawNewsV1) -> None:
    await bus.send(Topic.NEWS_RAW.value, key=message.event_id, value=encode_message(message))


def _dead_letters(bus: InMemoryBus) -> list[DeadLetterV1]:
    return [
        DeadLetterV1.model_validate_json(record.value)
        for record in bus.records(Topic.DEAD_LETTER.value)
    ]


# ------------------------------------------------------------------------ bus


async def test_a_new_subscription_resumes_from_the_committed_offset() -> None:
    bus = InMemoryBus()
    for index in range(3):
        await bus.send("t", key=str(index), value=str(index).encode())

    first = await bus.subscribe(group=GROUP, topics=["t"])
    record = await first.poll(0)
    assert record is not None
    await first.commit(record)
    uncommitted = await first.poll(0)
    assert uncommitted is not None
    assert uncommitted.offset == 1

    restarted = await bus.subscribe(group=GROUP, topics=["t"])
    redelivered = await restarted.poll(0)
    assert redelivered is not None
    assert redelivered.offset == 1, "the uncommitted record must be delivered again"

    other_group = await bus.subscribe(group="another", topics=["t"])
    fresh = await other_group.poll(0)
    assert fresh is not None
    assert fresh.offset == 0, "groups track offsets independently"


async def test_poll_times_out_and_wakes_on_a_new_record() -> None:
    bus = InMemoryBus()
    consumer = await bus.subscribe(group=GROUP, topics=["t"])
    assert await consumer.poll(0.01) is None
    await bus.send("t", key="k", value=b"v", headers={"h": "1"})
    record = await consumer.poll(1.0)
    assert record == Record(
        topic="t", partition=0, offset=0, key="k", value=b"v", headers={"h": "1"}
    )


# --------------------------------------------------------------------- worker


async def test_success_publishes_outputs_then_commits() -> None:
    bus, handler = InMemoryBus(), Recorder()
    await _send(bus, make_raw())
    assert await _worker(bus, handler).drain() == 1
    assert len(bus.records(Topic.EVENTS_NORMALIZED.value)) == 1
    assert bus.committed(GROUP, Topic.NEWS_RAW.value) == 1
    headers = bus.records(Topic.EVENTS_NORMALIZED.value)[0].headers
    assert headers["trace_id"] == make_raw().trace_id
    assert headers["schema"] == "RawNewsV1"


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"{not json", DeadLetterReason.INVALID_JSON),
        (b'{"event_id": "x"}', DeadLetterReason.VALIDATION_ERROR),
    ],
)
async def test_unparseable_records_are_quarantined_without_calling_the_handler(
    payload: bytes, reason: DeadLetterReason
) -> None:
    bus, handler = InMemoryBus(), Recorder()
    await bus.send(Topic.NEWS_RAW.value, key="bad", value=payload)
    await _send(bus, make_raw())

    assert await _worker(bus, handler).drain() == 2
    assert handler.seen == [make_raw().event_id], "the good record behind it still flows"
    (dead,) = _dead_letters(bus)
    assert dead.reason is reason
    assert dead.payload == payload.decode()
    assert (dead.original_topic, dead.original_offset, dead.original_key) == (
        "news.raw.v1",
        0,
        "bad",
    )
    assert bus.committed(GROUP, Topic.NEWS_RAW.value) == 2


async def test_transient_failures_are_retried_until_success() -> None:
    bus = InMemoryBus()
    handler = Recorder(failures=[RuntimeError("blip"), RuntimeError("blip")])
    await _send(bus, make_raw())
    await _worker(bus, handler).drain()
    assert len(handler.seen) == 3
    assert len(bus.records(Topic.EVENTS_NORMALIZED.value)) == 1
    assert _dead_letters(bus) == []


async def test_a_poison_record_is_quarantined_after_the_retry_budget() -> None:
    bus = InMemoryBus()
    handler = Recorder(failures=[RuntimeError("always")] * 3)
    await _send(bus, make_raw())
    await _worker(bus, handler).drain()
    assert len(handler.seen) == 3
    (dead,) = _dead_letters(bus)
    assert dead.reason is DeadLetterReason.HANDLER_ERROR
    assert "always" in dead.error_detail
    assert json.loads(dead.payload)["event_id"] == make_raw().event_id
    assert bus.records(Topic.EVENTS_NORMALIZED.value) == []
    assert bus.committed(GROUP, Topic.NEWS_RAW.value) == 1


async def test_reject_message_skips_retries() -> None:
    bus = InMemoryBus()
    handler = Recorder(failures=[RejectMessage("empty after cleaning")])
    await _send(bus, make_raw())
    await _worker(bus, handler).drain()
    assert len(handler.seen) == 1
    (dead,) = _dead_letters(bus)
    assert dead.reason is DeadLetterReason.VALIDATION_ERROR


async def test_infrastructure_failure_crashes_instead_of_quarantining_good_data() -> None:
    bus = InMemoryBus()
    handler = Recorder(failures=[ConnectionError("db down")] * 3)
    await _send(bus, make_raw())
    worker = _worker(bus, handler, infrastructure_errors=(ConnectionError,))
    with pytest.raises(ConnectionError):
        await worker.drain()
    assert _dead_letters(bus) == []
    assert bus.committed(GROUP, Topic.NEWS_RAW.value) == 0, "left uncommitted for redelivery"

    recovered = Recorder()
    await _worker(bus, recovered).drain()
    assert recovered.seen == [make_raw().event_id]


async def test_crash_after_publish_before_commit_redelivers_the_same_output() -> None:
    bus, handler = InMemoryBus(), Recorder()
    await _send(bus, make_raw())
    worker = _worker(bus, handler)
    await worker.start()
    consumer = worker._require_consumer()

    async def commit_fails(_record: Record) -> None:
        raise ConnectionError("broker went away before the commit")

    consumer.commit = commit_fails  # type: ignore[method-assign]
    record = await consumer.poll(0)
    assert record is not None
    with pytest.raises(ConnectionError):
        await worker.process(record)

    await _worker(bus, Recorder()).drain()
    first, second = bus.records(Topic.EVENTS_NORMALIZED.value)
    assert first.value == second.value, "redelivery must reproduce the output byte for byte"
    assert first.key == second.key


async def test_an_unprocessable_dead_letter_is_dropped_not_requeued() -> None:
    bus = InMemoryBus()

    async def never_called(_message: DeadLetterV1) -> Sequence[Outbound]:
        raise AssertionError("handler must not run for an invalid record")

    worker = StreamWorker(
        service="test",
        bus=bus,
        group=GROUP,
        routes={Topic.DEAD_LETTER: Route(DeadLetterV1, never_called)},
    )
    await bus.send(Topic.DEAD_LETTER.value, key="k", value=b"{broken")
    assert await worker.drain() == 1
    assert len(bus.records(Topic.DEAD_LETTER.value)) == 1, "no second dead letter was produced"
    assert bus.committed(GROUP, Topic.DEAD_LETTER.value) == 1


def test_backoff_is_jittered_and_capped() -> None:
    policy = RetryPolicy(max_attempts=5, base_delay_seconds=1.0, max_delay_seconds=3.0)
    rng = random.Random(7)
    for attempt, ceiling in [(1, 1.0), (2, 2.0), (3, 3.0), (6, 3.0)]:
        delays = [policy.delay(attempt, rng) for _ in range(200)]
        assert all(0.0 <= delay <= ceiling for delay in delays)
        assert max(delays) > ceiling * 0.8, "jitter should span the range, not cluster low"
