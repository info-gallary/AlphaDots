"""Entry point: ``python -m alphadots_preprocessor``."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta

from alphadots_common.bus.base import EventBus
from alphadots_common.config import Settings
from alphadots_common.db import make_engine
from alphadots_common.reference import InstrumentMaster
from alphadots_common.service import retry_policy, run_service
from alphadots_common.worker import StreamWorker
from alphadots_preprocessor.dedup import SqlDedupStore
from alphadots_preprocessor.entities import EntityResolver
from alphadots_preprocessor.processor import SERVICE, Preprocessor, build_worker


async def build(settings: Settings, bus: EventBus, stack: AsyncExitStack) -> StreamWorker:
    engine = make_engine(settings.database_url)
    stack.push_async_callback(engine.dispose)
    dedup = SqlDedupStore(engine)
    await dedup.create_schema()
    master = InstrumentMaster.load(settings.fixtures_dir / "instruments.json")
    preprocessor = Preprocessor(
        resolver=EntityResolver(master),
        dedup=dedup,
        stage_allowance=timedelta(seconds=settings.stage_latency_allowance_seconds),
    )
    return build_worker(bus=bus, preprocessor=preprocessor, retry=retry_policy(settings))


def main() -> None:
    asyncio.run(run_service(Settings(service_name=SERVICE), build))


if __name__ == "__main__":
    main()
