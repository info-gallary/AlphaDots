"""Process entry point shared by long-running stream services."""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack

from alphadots_common.bus.base import EventBus
from alphadots_common.bus.memory import InMemoryBus
from alphadots_common.config import Settings
from alphadots_common.logging import configure_logging, get_logger
from alphadots_common.metrics import start_metrics_server
from alphadots_common.worker import RetryPolicy, StreamWorker

WorkerFactory = Callable[[Settings, EventBus, AsyncExitStack], Awaitable[StreamWorker]]


def make_bus(settings: Settings) -> EventBus:
    if settings.bus_backend == "kafka":
        # Imported lazily so the in-memory path never needs a broker client loaded.
        from alphadots_common.bus.kafka import KafkaBus  # noqa: PLC0415

        return KafkaBus(settings.kafka_bootstrap, client_id=settings.service_name)
    return InMemoryBus()


def retry_policy(settings: Settings) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_delay_seconds=settings.retry_base_delay_seconds,
    )


def install_stop_handlers(stop: asyncio.Event) -> None:
    """Set ``stop`` on SIGINT/SIGTERM so the worker finishes its record and exits cleanly."""
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:  # Windows event loops
            with contextlib.suppress(ValueError):
                signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop.set))


async def run_service(settings: Settings, build: WorkerFactory) -> None:
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    log = get_logger(settings.service_name)
    start_metrics_server(settings.metrics_port)
    stop = asyncio.Event()
    install_stop_handlers(stop)
    bus = make_bus(settings)
    async with AsyncExitStack() as stack:
        await bus.start()
        stack.push_async_callback(bus.stop)
        worker = await build(settings, bus, stack)
        log.info("service_started", service=settings.service_name, bus=settings.bus_backend)
        await worker.run(stop)
    log.info("service_stopped", service=settings.service_name)
