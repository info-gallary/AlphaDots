"""The same pipeline on a real broker and a real database.

Run with ``make test-integration``, which starts throwaway Redpanda and PostgreSQL
containers from ``infra/compose/docker-compose.test.yml`` and tears them down afterwards.

What this adds over the in-memory end-to-end tests: the Kafka client path (manual offset
commits, consumer groups resuming after a restart), PostgreSQL upserts and timestamp
handling, and a direct check that both runtimes produce the same API output.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from aiokafka import AIOKafkaConsumer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError
from sqlalchemy import Column, MetaData, String, Table, select
from sqlalchemy.ext.asyncio import AsyncEngine

from alphadots_api import projection
from alphadots_api import tables as api_tables
from alphadots_api.app import create_app
from alphadots_api.read_model import ReadModel
from alphadots_common.bus.kafka import KafkaBus
from alphadots_common.db import UtcDateTime, make_engine
from alphadots_common.reference import InstrumentMaster
from alphadots_common.service import retry_policy
from alphadots_common.worker import StreamWorker
from alphadots_contracts import TOPIC_MODELS, Topic
from alphadots_ingestion.adapters import FixtureReplayAdapter
from alphadots_ingestion.replay import replay
from alphadots_preprocessor import dedup
from alphadots_preprocessor import processor as preprocessor
from alphadots_preprocessor.entities import EntityResolver
from alphadots_sentiment import processor as sentiment
from alphadots_signal_engine import processor as signal_engine
from alphadots_signal_engine import store as signal_store
from scripts.local_stack import LocalStack
from tests.conftest import FIXTURES_DIR, SEED, T0, api_client, make_settings, snapshot_tables

pytestmark = pytest.mark.integration

# Read at import time: the session fixture that isolates tests clears ALPHADOTS_* later.
KAFKA = os.environ.get("ALPHADOTS_IT_KAFKA_BOOTSTRAP", "localhost:29092")
DATABASE_URL = os.environ.get(
    "ALPHADOTS_IT_DATABASE_URL",
    "postgresql+asyncpg://alphadots:alphadots-it@localhost:25432/alphadots_it",
)
SETTLE_TIMEOUT_SECONDS = 90.0
# Consumer groups in pipeline order, with the topics each one reads.
STAGES: tuple[tuple[str, tuple[Topic, ...]], ...] = (
    (preprocessor.CONSUMER_GROUP, (Topic.NEWS_RAW,)),
    (sentiment.CONSUMER_GROUP, (Topic.EVENTS_NORMALIZED,)),
    (signal_engine.CONSUMER_GROUP, (Topic.SENTIMENT_SCORED,)),
    (
        projection.CONSUMER_GROUP,
        (
            Topic.EVENTS_NORMALIZED,
            Topic.SENTIMENT_SCORED,
            Topic.SIGNALS_GENERATED,
            Topic.DEAD_LETTER,
        ),
    ),
)
ALL_METADATA = (api_tables.metadata, dedup.metadata, signal_store.metadata)


async def create_topics(names: list[str]) -> None:
    admin = AIOKafkaAdminClient(bootstrap_servers=KAFKA)
    await admin.start()
    try:
        for name in names:
            with contextlib.suppress(TopicAlreadyExistsError):
                await admin.create_topics(
                    [NewTopic(name=name, num_partitions=1, replication_factor=1)]
                )
    finally:
        await admin.close()


async def total_lag() -> int:
    """Uncommitted records across every stage, read in pipeline order.

    A stage publishes its outputs before it commits its input. So if each stage in turn
    shows zero lag, and nothing new is being ingested, the pipeline is quiescent.
    """
    admin = AIOKafkaAdminClient(bootstrap_servers=KAFKA)
    probe = AIOKafkaConsumer(bootstrap_servers=KAFKA)
    await admin.start()
    await probe.start()
    try:
        lag = 0
        for group, topics in STAGES:
            partitions = [TopicPartition(topic.value, 0) for topic in topics]
            committed = await admin.list_consumer_group_offsets(group)
            ends = await probe.end_offsets(partitions)
            for partition in partitions:
                done = committed[partition].offset if partition in committed else 0
                lag += ends[partition] - max(done, 0)
        return lag
    finally:
        await probe.stop()
        await admin.close()


async def wait_until_quiescent() -> None:
    deadline = time.monotonic() + SETTLE_TIMEOUT_SECONDS
    lag = -1
    while time.monotonic() < deadline:
        lag = await total_lag()
        if lag == 0:
            return
        await asyncio.sleep(0.25)
    raise AssertionError(f"pipeline did not settle within {SETTLE_TIMEOUT_SECONDS}s (lag={lag})")


@dataclass
class KafkaPipeline:
    """The four stream stages, each with its own Kafka client, sharing one PostgreSQL."""

    engine: AsyncEngine
    read_model: ReadModel
    master: InstrumentMaster
    buses: list[KafkaBus] = field(default_factory=list)
    tasks: list[asyncio.Task[None]] = field(default_factory=list)
    stop: asyncio.Event = field(default_factory=asyncio.Event)

    async def start(self) -> None:
        settings = make_settings()
        allowance = timedelta(seconds=settings.stage_latency_allowance_seconds)
        retry = retry_policy(settings)

        async def bus(name: str) -> KafkaBus:
            instance = KafkaBus(KAFKA, client_id=f"it-{name}")
            await instance.start()
            self.buses.append(instance)
            return instance

        workers: list[StreamWorker] = [
            preprocessor.build_worker(
                bus=await bus("preprocessor"),
                preprocessor=preprocessor.Preprocessor(
                    resolver=EntityResolver(self.master),
                    dedup=dedup.SqlDedupStore(self.engine),
                    stage_allowance=allowance,
                ),
                retry=retry,
            ),
            sentiment.build_worker(
                bus=await bus("sentiment"),
                processor=sentiment.SentimentProcessor(stage_allowance=allowance),
                retry=retry,
            ),
            signal_engine.build_worker(
                bus=await bus("signal-engine"),
                engine=signal_engine.SignalEngine(
                    store=signal_store.SqlScoredEventStore(self.engine),
                    config=signal_engine.SignalConfig.from_settings(settings),
                ),
                retry=retry,
            ),
            projection.build_projection_worker(
                bus=await bus("api"), read_model=self.read_model, retry=retry
            ),
        ]
        self.stop = asyncio.Event()
        self.tasks = [asyncio.create_task(worker.run(self.stop)) for worker in workers]

    async def shutdown(self) -> None:
        self.stop.set()
        await asyncio.gather(*self.tasks)
        for instance in self.buses:
            await instance.stop()
        self.buses.clear()
        self.tasks.clear()

    async def ingest_seed(self) -> None:
        producer = KafkaBus(KAFKA, client_id="it-ingestion")
        await producer.start()
        try:
            adapter = FixtureReplayAdapter(FIXTURES_DIR / SEED, fixtures_root=FIXTURES_DIR)
            await replay(adapter, producer)
        finally:
            await producer.stop()


@pytest.fixture(scope="module")
async def engine() -> AsyncIterator[AsyncEngine]:
    instance = make_engine(DATABASE_URL)
    async with instance.begin() as conn:
        for metadata in ALL_METADATA:
            # Start from empty tables so a rerun against a live database is repeatable.
            await conn.run_sync(metadata.drop_all)
            await conn.run_sync(metadata.create_all)
    yield instance
    await instance.dispose()


@pytest.fixture(scope="module")
async def pipeline(engine: AsyncEngine) -> AsyncIterator[KafkaPipeline]:
    await create_topics([topic.value for topic in TOPIC_MODELS])
    instance = KafkaPipeline(
        engine=engine,
        read_model=ReadModel(engine),
        master=InstrumentMaster.load(FIXTURES_DIR / "instruments.json"),
    )
    await instance.start()
    await instance.ingest_seed()
    await wait_until_quiescent()
    yield instance
    await instance.shutdown()


# --------------------------------------------------------------------- bus


async def test_kafka_consumer_group_resumes_from_its_committed_offset() -> None:
    topic = f"it.resume.{uuid.uuid4().hex}"
    group = f"it.group.{uuid.uuid4().hex}"
    await create_topics([topic])
    bus = KafkaBus(KAFKA, client_id="it-bus")
    await bus.start()
    try:
        for index in range(3):
            await bus.send(topic, key=f"k{index}", value=f"v{index}".encode(), headers={"n": "1"})

        first = await bus.subscribe(group=group, topics=[topic])
        record = await first.poll(15.0)
        assert record is not None
        assert (record.offset, record.key, record.value, record.headers) == (
            0,
            "k0",
            b"v0",
            {"n": "1"},
        )
        await first.commit(record)
        uncommitted = await first.poll(15.0)
        assert uncommitted is not None
        assert uncommitted.offset == 1
        await first.close()

        restarted = await bus.subscribe(group=group, topics=[topic])
        redelivered = await restarted.poll(15.0)
        assert redelivered is not None
        assert redelivered.offset == 1, "the uncommitted record is delivered again"
        assert redelivered.value == b"v1"
    finally:
        await bus.stop()


async def test_kafka_poll_returns_none_when_the_topic_is_idle() -> None:
    topic = f"it.idle.{uuid.uuid4().hex}"
    await create_topics([topic])
    bus = KafkaBus(KAFKA, client_id="it-idle")
    await bus.start()
    try:
        consumer = await bus.subscribe(group=f"it.group.{uuid.uuid4().hex}", topics=[topic])
        assert await consumer.poll(1.0) is None
    finally:
        await bus.stop()


async def test_send_before_start_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="before start"):
        await KafkaBus(KAFKA, client_id="it-unstarted").send("t", key="k", value=b"v")


# ---------------------------------------------------------------- postgres


async def test_postgres_timestamps_round_trip_as_utc(engine: AsyncEngine) -> None:
    metadata = MetaData()
    table = Table(
        f"it_stamps_{uuid.uuid4().hex[:12]}",
        metadata,
        Column("id", String, primary_key=True),
        Column("at", UtcDateTime(), nullable=False),
    )
    eastern = datetime(2026, 3, 2, 9, 0, tzinfo=timezone(timedelta(hours=-5)))
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        await conn.execute(table.insert().values(id="a", at=eastern))
        stored = (await conn.execute(select(table.c.at))).scalar_one()
        earlier = (
            await conn.execute(select(table.c.id).where(table.c.at <= T0 - timedelta(seconds=1)))
        ).all()
        await conn.run_sync(metadata.drop_all)
    assert stored == datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
    assert stored.utcoffset() == timedelta(0)
    assert earlier == [], "a point-in-time comparison is evaluated in UTC by the database"


async def test_concurrent_dedup_claims_agree_on_one_canonical_event(engine: AsyncEngine) -> None:
    store = dedup.SqlDedupStore(engine)
    fingerprint = uuid.uuid4().hex + uuid.uuid4().hex
    claims = await asyncio.gather(
        *(store.claim(fingerprint, f"event-{index}", T0) for index in range(25))
    )
    assert len(set(claims)) == 1, "every claimant must be told the same winner"
    assert claims[0].startswith("event-")
    assert await store.claim(fingerprint, "latecomer", T0) == claims[0]


# ---------------------------------------------------------------- pipeline


def _without_generated_at(body: Any) -> Any:
    body["meta"].pop("generated_at")
    return body


async def test_kafka_and_postgres_give_the_same_api_output_as_the_in_memory_run(
    pipeline: KafkaPipeline, seeded: LocalStack
) -> None:
    app = create_app(
        settings=make_settings(), read_model=pipeline.read_model, master=pipeline.master
    )
    paths = [
        "/v1/events?limit=200",
        "/v1/overview",
        "/v1/signals/NWSC",
        "/v1/signals/APXE",
        "/v1/sentiment/CBHB?window=7d&as_of=2026-03-03T00:00:00Z",
        "/v1/events?as_of=2026-03-02T15:00:00Z",
    ]
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://it") as real,
        api_client(seeded) as memory,
    ):
        for path in paths:
            from_kafka = (await real.get(path)).json()
            from_memory = (await memory.get(path)).json()
            assert _without_generated_at(from_kafka) == _without_generated_at(from_memory), path
        ready = await real.get("/health/ready")
        assert ready.json()["checks"]["database"] == "ok"


async def test_restart_and_full_redelivery_leave_postgres_unchanged(
    pipeline: KafkaPipeline,
) -> None:
    before = await snapshot_tables(pipeline.engine)
    assert len(before["api_events"]) == 16
    assert len(before["api_signals"]) == 17

    await pipeline.shutdown()  # every stage stops, as on a rolling restart
    await pipeline.ingest_seed()  # the whole fixture is published a second time
    assert await total_lag() > 0, "records are waiting for the restarted consumers"
    await pipeline.start()
    await wait_until_quiescent()

    assert await snapshot_tables(pipeline.engine) == before
