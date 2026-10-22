"""Entry point: ``python -m alphadots_signal_engine``."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

from alphadots_common.bus.base import EventBus
from alphadots_common.config import Settings
from alphadots_common.db import make_engine
from alphadots_common.service import retry_policy, run_service
from alphadots_common.worker import StreamWorker
from alphadots_signal_engine.processor import SERVICE, SignalConfig, SignalEngine, build_worker
from alphadots_signal_engine.store import SqlScoredEventStore


async def build(settings: Settings, bus: EventBus, stack: AsyncExitStack) -> StreamWorker:
    engine = make_engine(settings.database_url)
    stack.push_async_callback(engine.dispose)
    store = SqlScoredEventStore(engine)
    await store.create_schema()
    return build_worker(
        bus=bus,
        engine=SignalEngine(store=store, config=SignalConfig.from_settings(settings)),
        retry=retry_policy(settings),
    )


def main() -> None:
    asyncio.run(run_service(Settings(service_name=SERVICE), build))


if __name__ == "__main__":
    main()
