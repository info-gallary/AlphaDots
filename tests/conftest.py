"""Shared fixtures. Tests never read the developer's environment or ``.env``."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from alphadots_api import tables as api_tables
from alphadots_common.config import Settings
from alphadots_common.ids import derive_id, sha256_hex, trace_id_for
from alphadots_common.reference import InstrumentMaster
from alphadots_contracts import (
    EventType,
    InstrumentRef,
    NormalizedEventV1,
    PolarityScores,
    RawNewsV1,
    SentimentScoredV1,
)
from alphadots_preprocessor import dedup
from alphadots_signal_engine import store as signal_store
from scripts.local_stack import LocalStack

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "data" / "fixtures"
SEED = Path("news/seed.jsonl")
REPLAY = Path("news/replay.jsonl")
T0 = datetime(2026, 3, 2, 14, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="session", autouse=True)
def _isolated_environment() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as monkeypatch:
        for name in list(os.environ):
            if name.startswith("ALPHADOTS_"):
                monkeypatch.delenv(name)
        yield


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "bus_backend": "memory",
        "fixtures_dir": FIXTURES_DIR,
        "retry_base_delay_seconds": 0.0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture(scope="session")
def master() -> InstrumentMaster:
    return InstrumentMaster.load(FIXTURES_DIR / "instruments.json")


@pytest.fixture
async def stack(settings: Settings) -> AsyncIterator[LocalStack]:
    """A fresh, empty stack for tests that change state."""
    instance = await LocalStack.create(settings)
    yield instance
    await instance.close()


@pytest.fixture(scope="session")
async def seeded(_isolated_environment: None) -> AsyncIterator[LocalStack]:
    """Shared stack that has processed the curated seed fixture. Read-only: do not mutate."""
    instance = await LocalStack.create(make_settings())
    await instance.ingest(SEED)
    await instance.drain()
    yield instance
    await instance.close()


@pytest.fixture(scope="session")
async def loaded(_isolated_environment: None) -> AsyncIterator[LocalStack]:
    """Shared stack that processed the seed file and then the replay file, which is out of
    arrival order on purpose. Read-only: do not mutate."""
    instance = await LocalStack.create(make_settings())
    await instance.ingest(SEED)
    await instance.drain()
    await instance.ingest(REPLAY)
    await instance.drain()
    yield instance
    await instance.close()


# Dead letters carry a wall-clock failed_at, so they are compared by key, not by value.
STATE_TABLES = (
    api_tables.events,
    api_tables.event_instruments,
    api_tables.sentiment,
    api_tables.signals,
    dedup.content_seen,
    signal_store.scored_events,
)


async def snapshot_tables(engine: AsyncEngine) -> dict[str, list[tuple[Any, ...]]]:
    """Every row of every service-owned table, in a deterministic order."""
    state: dict[str, list[tuple[Any, ...]]] = {}
    async with engine.connect() as conn:
        for table in STATE_TABLES:
            rows = (await conn.execute(select(table))).all()
            state[table.name] = sorted((tuple(row) for row in rows), key=repr)
        dead = await conn.execute(select(api_tables.dead_letters.c.dlq_id))
        state["api_dead_letters.ids"] = sorted((row[0],) for row in dead)
    return state


def api_client(stack: LocalStack, **headers: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stack.app()), base_url="http://test", headers=headers
    )


@pytest.fixture
async def client(seeded: LocalStack) -> AsyncIterator[httpx.AsyncClient]:
    async with api_client(seeded) as instance:
        yield instance


# ---------------------------------------------------------------- message builders


def make_raw(
    *,
    source_event_id: str = "t-1",
    headline: str = "Northwind Semiconductor beats estimates",
    body: str | None = None,
    first_seen_at: datetime = T0,
    published_at: datetime | None = None,
    provider_symbols: tuple[str, ...] = ("NWSC",),
    language: str | None = "en",
    source: str = "test_wire",
) -> RawNewsV1:
    event_id = derive_id("raw", source, source_event_id)
    ingested = first_seen_at + timedelta(seconds=1)
    return RawNewsV1(
        event_id=event_id,
        trace_id=trace_id_for(event_id),
        available_at=ingested,
        source=source,
        source_event_id=source_event_id,
        content_hash=sha256_hex(headline, body or ""),
        source_url=f"https://test.example.invalid/{source_event_id}",
        published_at=published_at or first_seen_at - timedelta(seconds=4),
        first_seen_at=first_seen_at,
        ingested_at=ingested,
        source_license="CC0-1.0-synthetic",
        payload_ref=f"fixture://test#{source_event_id}",
        headline=headline,
        body=body,
        language=language,
        provider_symbols=provider_symbols,
    )


def make_normalized(
    *,
    event_key: str = "n-1",
    headline: str = "Northwind Semiconductor beats estimates",
    body: str | None = None,
    available_at: datetime = T0,
    instruments: tuple[InstrumentRef, ...] | None = None,
    language: str = "en",
    duplicate_of: str | None = None,
    source: str = "test_wire",
) -> NormalizedEventV1:
    event_id = derive_id("norm", event_key)
    first_seen = available_at - timedelta(seconds=2)
    return NormalizedEventV1(
        event_id=event_id,
        trace_id=trace_id_for(event_id),
        available_at=available_at,
        source=source,
        source_event_id=event_key,
        content_hash=sha256_hex(headline, body or ""),
        source_url=f"https://test.example.invalid/{event_key}",
        published_at=first_seen - timedelta(seconds=4),
        first_seen_at=first_seen,
        ingested_at=first_seen + timedelta(seconds=1),
        instruments=(
            instruments
            if instruments is not None
            else (InstrumentRef(symbol="NWSC", instrument_id="SYN-0001", confidence=0.98),)
        ),
        source_license="CC0-1.0-synthetic",
        payload_ref=f"fixture://test#{event_key}",
        raw_event_id=derive_id("raw", event_key),
        headline=headline,
        body=body,
        language=language,
        cluster_id=f"clu_{event_key}",
        duplicate_of=duplicate_of,
        normalizer_version="norm-test",
    )


def make_scored(
    *,
    event_key: str = "s-1",
    available_at: datetime = T0,
    signed_sentiment: float = 0.5,
    instruments: tuple[InstrumentRef, ...] | None = None,
    materiality: float = 1.0,
    abstained: bool = False,
    novelty: float = 1.0,
    source: str = "test_wire",
) -> SentimentScoredV1:
    event_id = derive_id("sent", event_key)
    first_seen = available_at - timedelta(seconds=3)
    positive = max(signed_sentiment, 0.0)
    negative = max(-signed_sentiment, 0.0)
    scores = (
        PolarityScores(negative=1 / 3, neutral=1 / 3, positive=1 / 3)
        if abstained
        else PolarityScores(negative=negative, neutral=1.0 - positive - negative, positive=positive)
    )
    return SentimentScoredV1(
        event_id=event_id,
        trace_id=trace_id_for(event_id),
        available_at=available_at,
        source=source,
        source_event_id=event_key,
        content_hash=sha256_hex(event_key),
        source_url=f"https://test.example.invalid/{event_key}",
        published_at=first_seen - timedelta(seconds=4),
        first_seen_at=first_seen,
        ingested_at=first_seen + timedelta(seconds=1),
        instruments=(
            instruments
            if instruments is not None
            else (InstrumentRef(symbol="NWSC", instrument_id="SYN-0001", confidence=1.0),)
        ),
        source_license="CC0-1.0-synthetic",
        payload_ref=f"fixture://test#{event_key}",
        normalized_event_id=derive_id("norm", event_key),
        model_id="test-model",
        model_version="test-0",
        calibration_version="uncalibrated",
        scores=scores,
        signed_sentiment=0.0 if abstained else signed_sentiment,
        entropy=1.0 if abstained else 0.5,
        abstained=abstained,
        abstain_reason="no_lexicon_match" if abstained else None,  # type: ignore[arg-type]
        event_type=EventType.EARNINGS,
        materiality=materiality,
        novelty=novelty,
    )
