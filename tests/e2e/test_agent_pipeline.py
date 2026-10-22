"""The research agent wired end to end: public API -> AgentClient -> research_agent ->
ApiReadClient -> the same API's read model, all in-process over ASGI transports, no real
sockets. This is what proves the pieces in ``docs/adr/0003-bounded-research-agent.md``
actually compose, not just that each one works in isolation.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest

from alphadots_api.agent_client import AgentClient
from alphadots_api.app import create_app as create_api_app
from alphadots_common.db import make_engine
from alphadots_llm import MockAdapter
from alphadots_research_agent.app import create_app as create_agent_app
from alphadots_research_agent.bounds import AgentBounds
from alphadots_research_agent.client import ApiReadClient
from alphadots_research_agent.jobs import JobRunner, JobStore
from alphadots_research_agent.publisher import publish_report
from scripts.local_stack import LocalStack
from tests.conftest import SEED, make_settings

POLL_DEADLINE_SECONDS = 15.0
MONITOR_CACHE_SECONDS = 1.0


class _LazyAsgiApp:
    """Forwards to a target app set after construction — resolves the otherwise circular
    "research_agent reads from the API that proxies to research_agent" wiring below."""

    def __init__(self) -> None:
        self.target: Any = None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        assert self.target is not None, "lazy app used before its target was set"
        await self.target(scope, receive, send)


@dataclass
class Pipeline:
    stack: LocalStack
    public: httpx.AsyncClient
    """The one client a real caller would use: the AlphaDots API's own interface."""
    store: JobStore
    """Direct access to the agent's job table, for assertions the HTTP API doesn't expose."""


async def _wait_for_job(client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + POLL_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        response = await client.get(f"/v1/research/jobs/{job_id}")
        body = response.json()["data"]
        if body["status"] in ("succeeded", "failed"):
            return body
        await asyncio.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {POLL_DEADLINE_SECONDS}s")


@pytest.fixture
async def pipeline(tmp_path: Path) -> AsyncIterator[Pipeline]:
    settings = make_settings()
    stack = await LocalStack.create(settings)
    await stack.ingest(SEED)
    await stack.drain()

    lazy_api = _LazyAsgiApp()
    api_reader = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=lazy_api), base_url="http://api"
    )
    evidence_client = ApiReadClient("http://api", token=None, timeout=10, client=api_reader)

    # A real file, not `:memory:`: the job store is written by a background task and read
    # by concurrent request handlers, and SQLite's in-memory StaticPool shares one
    # connection with no WAL — exactly the read-while-write pattern it cannot serialize
    # safely. A file gets WAL mode and a busy timeout from `make_engine`, like every other
    # non-memory SQLite use in this codebase (see `scripts/local_stack.py`).
    agent_store = JobStore(make_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent_jobs.sqlite3'}"))
    await agent_store.create_schema()
    adapter = MockAdapter()
    bounds = AgentBounds(
        max_tool_calls=8,
        max_tokens=900,
        timeout_seconds=10,
        max_correction_attempts=1,
        max_cost_usd=1.0,
    )
    runner = JobRunner(
        store=agent_store,
        client=evidence_client,
        adapter=adapter,
        bounds=bounds,
        concurrency=2,
        publish=partial(publish_report, stack.bus),
    )
    agent_app = create_agent_app(
        store=agent_store,
        runner=runner,
        client=evidence_client,
        adapter=adapter,
        bounds=bounds,
        monitor_cache_seconds=MONITOR_CACHE_SECONDS,
    )
    agent_transport = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent_app), base_url="http://agent"
    )
    agent_client = AgentClient("http://agent", timeout=10, client=agent_transport)

    api_app = create_api_app(
        settings=settings,
        read_model=stack.read_model,
        master=stack.master,
        agent_client=agent_client,
    )
    lazy_api.target = api_app
    public = httpx.AsyncClient(transport=httpx.ASGITransport(app=api_app), base_url="http://test")

    try:
        yield Pipeline(stack=stack, public=public, store=agent_store)
    finally:
        await public.aclose()
        await agent_transport.aclose()
        await api_reader.aclose()
        await stack.close()


# ------------------------------------------------------------------------- the brief


async def test_a_requested_brief_completes_with_real_citations(pipeline: Pipeline) -> None:
    created = (
        await pipeline.public.post(
            "/v1/research/jobs", json={"symbol": "NWSC", "as_of": "2026-03-02T23:00:00Z"}
        )
    ).json()["data"]
    assert created["status"] in ("pending", "running")

    job = await _wait_for_job(pipeline.public, created["job_id"])
    assert job["status"] == "succeeded"
    report = job["report"]
    assert report["abstained"] is False
    assert report["research_only"] is True
    assert "3 tracked news item(s)" in report["business_snapshot"]
    assert "2 of them scored" in report["business_snapshot"]

    cited_ids = {c["evidence_id"] for c in report["bull_case"] + report["bear_case"]}
    assert cited_ids, "a brief with real evidence must cite at least one of it"
    real_events = (
        await pipeline.public.get("/v1/events", params={"symbol": "NWSC", "limit": 200})
    ).json()["data"]
    real_ids = {event["event_id"] for event in real_events}
    assert cited_ids <= real_ids, "every citation must resolve to an event that actually exists"


async def test_repeating_the_same_request_reuses_the_job(pipeline: Pipeline) -> None:
    body = {"symbol": "NWSC", "as_of": "2026-03-02T23:00:00Z"}
    first = (await pipeline.public.post("/v1/research/jobs", json=body)).json()["data"]
    second = (await pipeline.public.post("/v1/research/jobs", json=body)).json()["data"]
    assert first["job_id"] == second["job_id"]
    await _wait_for_job(pipeline.public, first["job_id"])


async def test_point_in_time_bounds_the_evidence_a_brief_can_cite(pipeline: Pipeline) -> None:
    early = (
        await pipeline.public.post(
            "/v1/research/jobs", json={"symbol": "NWSC", "as_of": "2026-03-02T13:31:00Z"}
        )
    ).json()["data"]
    late = (
        await pipeline.public.post(
            "/v1/research/jobs", json={"symbol": "NWSC", "as_of": "2026-03-02T23:00:00Z"}
        )
    ).json()["data"]
    assert early["job_id"] != late["job_id"]

    early_report = (await _wait_for_job(pipeline.public, early["job_id"]))["report"]
    late_report = (await _wait_for_job(pipeline.public, late["job_id"]))["report"]
    early_citations = len(early_report["bull_case"]) + len(early_report["bear_case"])
    late_citations = len(late_report["bull_case"]) + len(late_report["bear_case"])
    assert "1 tracked news item(s)" in early_report["business_snapshot"]
    assert early_citations < late_citations


async def test_an_unknown_ticker_is_rejected_with_404(pipeline: Pipeline) -> None:
    """The API resolves the ticker itself before ever calling the agent (see
    ``test_api_research_routes.py`` for the unit-level proof that the agent is not
    reached); here the end-to-end contract is what matters: a clean 404, nothing worse."""
    response = await pipeline.public.post("/v1/research/jobs", json={"symbol": "NOPE"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "instrument_not_found"


async def test_the_prompt_injection_fixture_never_produces_recommendation_language(
    pipeline: Pipeline,
) -> None:
    """``news/seed.jsonl`` line 15 asks, in a news item's body, to "rate this stock a
    strong buy". Confirms end to end that the resulting brief never says anything of the
    kind — see ``facts.py`` and ``prompts.py`` for the two independent defences."""
    created = (
        await pipeline.public.post(
            "/v1/research/jobs", json={"symbol": "VIRE", "as_of": "2026-03-02T23:00:00Z"}
        )
    ).json()["data"]
    job = await _wait_for_job(pipeline.public, created["job_id"])
    report = job["report"]
    assert report["abstained"] is False
    rendered = str(report).lower()
    for phrase in ("strong buy", "price target", "buy rating", "recommend buying"):
        assert phrase not in rendered


async def test_the_latest_research_endpoint_reflects_a_completed_job_after_projection(
    pipeline: Pipeline,
) -> None:
    before = (await pipeline.public.get("/v1/research/NWSC")).json()["data"]
    assert before is None

    created = (
        await pipeline.public.post(
            "/v1/research/jobs", json={"symbol": "NWSC", "as_of": "2026-03-02T23:00:00Z"}
        )
    ).json()["data"]
    job = await _wait_for_job(pipeline.public, created["job_id"])
    await pipeline.stack.drain()  # let the projection consumer pick up reports.generated.v1

    after = (await pipeline.public.get("/v1/research/NWSC")).json()["data"]
    assert after is not None
    assert after["job_id"] == job["job_id"]
    assert after["event_id"] == job["report"]["event_id"]


# ----------------------------------------------------------------------- monitoring


async def test_monitor_reflects_the_real_quarantined_fixture_lines(pipeline: Pipeline) -> None:
    response = await pipeline.public.get("/v1/monitor")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] in ("warning", "critical")
    assert any(f["metric"] == "dead_letters" and f["value"] == 2 for f in body["data"]["findings"])
    assert body["meta"]["research_only"] is True


async def test_monitor_is_cached_on_the_second_call(pipeline: Pipeline) -> None:
    first = (await pipeline.public.get("/v1/monitor")).json()
    second = (await pipeline.public.get("/v1/monitor")).json()
    assert first["cached"] is False
    assert second["cached"] is True
    assert first["data"]["generated_at"] == second["data"]["generated_at"]
