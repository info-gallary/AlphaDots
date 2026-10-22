"""Entry point: ``python -m alphadots_research_agent``.

Selects the LLM adapter once at startup: Groq if ``ALPHADOTS_GROQ_API_KEY`` is set (or
``ALPHADOTS_LLM_PROVIDER=groq`` forces it), the deterministic mock otherwise. Every
research brief and monitor summary this process produces logs which one it used.
"""

from __future__ import annotations

import asyncio
import sys
from functools import partial

import uvicorn

from alphadots_common.bus.base import EventBus
from alphadots_common.config import Settings
from alphadots_common.db import make_engine
from alphadots_common.logging import configure_logging, get_logger
from alphadots_common.metrics import start_metrics_server
from alphadots_common.service import make_bus
from alphadots_llm import GroqAdapter, LlmAdapter, MockAdapter, TokenRate
from alphadots_research_agent.app import create_app
from alphadots_research_agent.bounds import AgentBounds
from alphadots_research_agent.client import ApiReadClient
from alphadots_research_agent.jobs import JobRunner, JobStore
from alphadots_research_agent.publisher import publish_report

SERVICE = "research_agent"


def build_adapter(settings: Settings) -> LlmAdapter:
    use_groq = settings.llm_provider == "groq" or (
        settings.llm_provider == "auto" and settings.groq_api_key is not None
    )
    if not use_groq:
        return MockAdapter()
    if settings.groq_api_key is None:
        raise RuntimeError("ALPHADOTS_LLM_PROVIDER=groq requires ALPHADOTS_GROQ_API_KEY to be set")
    rate = None
    if (
        settings.groq_price_input_per_m_usd is not None
        and settings.groq_price_output_per_m_usd is not None
    ):
        rate = TokenRate(settings.groq_price_input_per_m_usd, settings.groq_price_output_per_m_usd)
    return GroqAdapter(
        api_key=settings.groq_api_key.get_secret_value(),
        model=settings.groq_model,
        base_url=settings.groq_base_url,
        rate=rate,
    )


async def serve(settings: Settings) -> int:
    log = get_logger(SERVICE)
    start_metrics_server(settings.metrics_port)

    engine = make_engine(settings.database_url)
    store = JobStore(engine)
    await store.create_schema()

    bus: EventBus = make_bus(settings)
    await bus.start()

    adapter = build_adapter(settings)
    client = ApiReadClient(
        settings.api_url,
        token=settings.api_token.get_secret_value() if settings.api_token else None,
        timeout=settings.research_agent_timeout_seconds,
    )
    bounds = AgentBounds.from_settings(settings)
    runner = JobRunner(
        store=store,
        client=client,
        adapter=adapter,
        bounds=bounds,
        concurrency=settings.research_agent_concurrency,
        publish=partial(publish_report, bus),
    )

    app = create_app(
        store=store,
        runner=runner,
        client=client,
        adapter=adapter,
        bounds=bounds,
        monitor_cache_seconds=settings.monitor_cache_seconds,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.research_agent_host,
            port=settings.research_agent_port,
            log_config=None,
        )
    )
    log.info(
        "service_started",
        service=SERVICE,
        port=settings.research_agent_port,
        llm_provider=adapter.provider,
        llm_model=adapter.model,
        api_url=settings.api_url,
    )
    try:
        await server.serve()
    finally:
        await runner.wait_idle()
        await bus.stop()
        await engine.dispose()
    log.info("service_stopped", service=SERVICE)
    return 0


def main() -> None:
    settings = Settings(service_name=SERVICE)
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    sys.exit(asyncio.run(serve(settings)))


if __name__ == "__main__":
    main()
