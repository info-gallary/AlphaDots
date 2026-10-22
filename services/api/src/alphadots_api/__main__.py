"""Entry point: ``python -m alphadots_api``.

Runs the HTTP server and the projection consumer side by side. If either stops, both
stop: a server whose projection has died would keep answering with stale data, so the
process exits non-zero and lets the orchestrator restart it.
"""

from __future__ import annotations

import asyncio
import sys

import uvicorn

from alphadots_api.app import create_app
from alphadots_api.projection import SERVICE, build_projection_worker
from alphadots_api.read_model import ReadModel
from alphadots_common.config import Settings
from alphadots_common.db import make_engine
from alphadots_common.logging import configure_logging, get_logger
from alphadots_common.reference import InstrumentMaster
from alphadots_common.service import make_bus, retry_policy


async def serve(settings: Settings) -> int:
    log = get_logger(SERVICE)
    engine = make_engine(settings.database_url)
    read_model = ReadModel(engine)
    await read_model.create_schema()
    master = InstrumentMaster.load(settings.fixtures_dir / "instruments.json")
    if settings.api_token is None:
        log.warning("api_auth_disabled", hint="set ALPHADOTS_API_TOKEN outside local development")

    bus = make_bus(settings)
    await bus.start()
    stop = asyncio.Event()
    worker = build_projection_worker(bus=bus, read_model=read_model, retry=retry_policy(settings))
    projection = asyncio.create_task(worker.run(stop), name="projection")

    app = create_app(
        settings=settings,
        read_model=read_model,
        master=master,
        projection_alive=lambda: not projection.done(),
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host=settings.api_host, port=settings.api_port, log_config=None)
    )
    http = asyncio.create_task(server.serve(), name="http")
    log.info("service_started", service=SERVICE, port=settings.api_port)

    await asyncio.wait({projection, http}, return_when=asyncio.FIRST_COMPLETED)
    stop.set()
    server.should_exit = True
    results = await asyncio.gather(projection, http, return_exceptions=True)
    await bus.stop()
    await engine.dispose()

    failures = [result for result in results if isinstance(result, BaseException)]
    for failure in failures:
        log.error("service_task_failed", error=repr(failure))
    log.info("service_stopped", service=SERVICE)
    return 1 if failures else 0


def main() -> None:
    settings = Settings(service_name=SERVICE)
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    sys.exit(asyncio.run(serve(settings)))


if __name__ == "__main__":
    main()
