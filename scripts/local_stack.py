"""The whole pipeline in one process on the in-memory bus. No Docker, no credentials.

    uv run python -m scripts.local_stack --serve                  # seed + replay, then serve
    uv run python -m scripts.local_stack --serve --speed 20000    # paced replay for a live tape

This wires the same service code that the containers run, each behind its own consumer
group and its own tables. What it does not exercise is a real broker: partitions,
rebalancing, network failure. Those are covered by the compose stack and integration tests.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from alphadots_api.app import create_app
from alphadots_api.projection import build_projection_worker
from alphadots_api.read_model import ReadModel
from alphadots_common.bus import InMemoryBus
from alphadots_common.config import Settings
from alphadots_common.db import make_engine
from alphadots_common.logging import configure_logging, get_logger
from alphadots_common.reference import InstrumentMaster
from alphadots_common.service import install_stop_handlers, retry_policy
from alphadots_common.timeutils import utc_now
from alphadots_common.worker import StreamWorker
from alphadots_ingestion.adapters import FixtureReplayAdapter, MergedAdapter
from alphadots_ingestion.replay import ReplayStats, replay
from alphadots_preprocessor import processor as preprocessor
from alphadots_preprocessor.dedup import SqlDedupStore
from alphadots_preprocessor.entities import EntityResolver
from alphadots_sentiment import processor as sentiment
from alphadots_signal_engine import processor as signal_engine
from alphadots_signal_engine.store import SqlScoredEventStore

DEFAULT_FIXTURES = (Path("news/seed.jsonl"), Path("news/replay.jsonl"))
LOCAL_DB_PATH = Path("var/local_stack.sqlite3")


@dataclass
class LocalStack:
    settings: Settings
    bus: InMemoryBus
    engine: AsyncEngine
    read_model: ReadModel
    master: InstrumentMaster
    workers: list[StreamWorker] = field(default_factory=list)

    @classmethod
    async def create(
        cls, settings: Settings, *, database_url: str = "sqlite+aiosqlite://"
    ) -> LocalStack:
        engine = make_engine(database_url)
        read_model = ReadModel(engine)
        await read_model.create_schema()
        await SqlDedupStore(engine).create_schema()
        await SqlScoredEventStore(engine).create_schema()
        stack = cls(
            settings=settings,
            bus=InMemoryBus(),
            engine=engine,
            read_model=read_model,
            master=InstrumentMaster.load(settings.fixtures_dir / "instruments.json"),
        )
        stack.workers = stack.build_workers()
        return stack

    def build_workers(self) -> list[StreamWorker]:
        """Fresh worker objects over the same stores, as after a process restart."""
        allowance = timedelta(seconds=self.settings.stage_latency_allowance_seconds)
        retry = retry_policy(self.settings)
        return [
            preprocessor.build_worker(
                bus=self.bus,
                preprocessor=preprocessor.Preprocessor(
                    resolver=EntityResolver(self.master),
                    dedup=SqlDedupStore(self.engine),
                    stage_allowance=allowance,
                ),
                retry=retry,
            ),
            sentiment.build_worker(
                bus=self.bus,
                processor=sentiment.SentimentProcessor(stage_allowance=allowance),
                retry=retry,
            ),
            signal_engine.build_worker(
                bus=self.bus,
                engine=signal_engine.SignalEngine(
                    store=SqlScoredEventStore(self.engine),
                    config=signal_engine.SignalConfig.from_settings(self.settings),
                ),
                retry=retry,
            ),
            build_projection_worker(bus=self.bus, read_model=self.read_model, retry=retry),
        ]

    async def restart_workers(self) -> None:
        for worker in self.workers:
            await worker.close()
        self.workers = self.build_workers()

    async def ingest(
        self,
        *fixtures: Path,
        speed: float = 0.0,
        clock: Callable[[], datetime] = utc_now,
    ) -> ReplayStats:
        """Publish ``fixtures`` interleaved in arrival order.

        ``clock`` stamps quarantined records only; event content never reads it.
        """
        adapter = MergedAdapter(
            [
                FixtureReplayAdapter(
                    self.settings.fixtures_dir / fixture,
                    fixtures_root=self.settings.fixtures_dir,
                    ingest_allowance=timedelta(
                        seconds=self.settings.stage_latency_allowance_seconds
                    ),
                )
                for fixture in fixtures
            ]
        )
        return await replay(adapter, self.bus, speed=speed, clock=clock)

    async def drain(self) -> int:
        """Run every stage until the pipeline is quiescent; return records processed."""
        total = 0
        while True:
            processed = 0
            for worker in self.workers:
                processed += await worker.drain()
            if processed == 0:
                return total
            total += processed

    def app(self) -> FastAPI:
        return create_app(settings=self.settings, read_model=self.read_model, master=self.master)

    async def close(self) -> None:
        for worker in self.workers:
            await worker.close()
        await self.engine.dispose()


async def _serve(args: argparse.Namespace) -> None:
    settings = Settings(service_name="local_stack", bus_backend="memory")
    configure_logging(level=settings.log_level, json_output=False)
    log = get_logger("local_stack")
    # A file database gives each concurrent stage its own connection. It is recreated on
    # every start because the in-memory bus forgets its log, so state must start empty too.
    for leftover in LOCAL_DB_PATH.parent.glob(f"{LOCAL_DB_PATH.name}*"):
        leftover.unlink()
    stack = await LocalStack.create(
        settings, database_url=f"sqlite+aiosqlite:///{LOCAL_DB_PATH.as_posix()}"
    )
    stop = asyncio.Event()
    install_stop_handlers(stop)
    worker_tasks = [asyncio.create_task(worker.run(stop)) for worker in stack.workers]

    async def feed() -> None:
        fixtures = tuple(args.fixture or DEFAULT_FIXTURES)
        stats = await stack.ingest(*fixtures, speed=args.speed)
        log.info(
            "fixtures_replayed",
            fixtures=[str(fixture) for fixture in fixtures],
            published=stats.published,
            quarantined=stats.quarantined,
        )

    feeder = asyncio.create_task(feed())
    server = uvicorn.Server(
        uvicorn.Config(stack.app(), host=settings.api_host, port=args.port, log_level="warning")
    )
    log.info("local_stack_serving", url=f"http://{settings.api_host}:{args.port}/docs")
    try:
        await server.serve()
    finally:
        stop.set()
        feeder.cancel()
        await asyncio.gather(feeder, *worker_tasks, return_exceptions=True)
        await stack.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serve", action="store_true", help="serve the API after wiring")
    parser.add_argument("--port", type=int, default=18000)
    parser.add_argument("--speed", type=float, default=0.0, help="event-time acceleration")
    parser.add_argument("--fixture", type=Path, action="append", help="repeatable; default: all")
    args = parser.parse_args()
    if not args.serve:
        parser.error("nothing to do; pass --serve")
    asyncio.run(_serve(args))


if __name__ == "__main__":
    main()
