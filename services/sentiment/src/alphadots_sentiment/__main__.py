"""Entry point: ``python -m alphadots_sentiment``."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta

from alphadots_common.bus.base import EventBus
from alphadots_common.config import Settings
from alphadots_common.service import retry_policy, run_service
from alphadots_common.worker import StreamWorker
from alphadots_sentiment.processor import SERVICE, SentimentProcessor, build_worker


async def build(settings: Settings, bus: EventBus, _stack: AsyncExitStack) -> StreamWorker:
    processor = SentimentProcessor(
        stage_allowance=timedelta(seconds=settings.stage_latency_allowance_seconds)
    )
    return build_worker(bus=bus, processor=processor, retry=retry_policy(settings))


def main() -> None:
    asyncio.run(run_service(Settings(service_name=SERVICE), build))


if __name__ == "__main__":
    main()
