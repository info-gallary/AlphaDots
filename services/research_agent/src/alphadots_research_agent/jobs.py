"""The research job store and its bounded runner.

A job's ID is derived from the ticker and its resolved ``as_of`` (see ``app.py``), so
submitting the same request twice reaches the same row instead of paying for a second
run — the "idempotent" half of "Research jobs must be asynchronous and idempotent".
Row creation is a database-level claim (insert-if-absent, then read back), safe under
concurrent requests the way ``preprocessor``'s dedup store is. Actually *running* a job is
tracked in-process only: this service runs as a single worker, so a plain ``set`` checked
with no ``await`` between check and insert is already race-free, and adding a real queue
for multi-worker execution is not justified yet (a documented simplification, not an
oversight — see ``docs/adr/0003-bounded-research-agent.md``). A job stuck "running" past
a crash is not resumed; this is a known limit of the same simplification.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, MetaData, String, Table, Text, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from alphadots_common.db import UtcDateTime
from alphadots_common.logging import get_logger
from alphadots_common.timeutils import utc_now
from alphadots_contracts import InstrumentRef, ReportGeneratedV1
from alphadots_llm import LlmAdapter
from alphadots_research_agent.bounds import AgentBounds
from alphadots_research_agent.client import ApiReadClient
from alphadots_research_agent.research_graph import run_research

_log = get_logger(__name__)
Row = dict[str, Any]
Publisher = Callable[[ReportGeneratedV1], Awaitable[None]]

metadata = MetaData()

agent_jobs = Table(
    "agent_jobs",
    metadata,
    Column("job_id", String(64), primary_key=True),
    Column("instrument_id", String(64), nullable=False),
    Column("symbol", String(16), nullable=False),
    Column("as_of", UtcDateTime(), nullable=False),
    Column("status", String(16), nullable=False),  # pending | running | succeeded | failed
    Column("created_at", UtcDateTime(), nullable=False),
    Column("started_at", UtcDateTime(), nullable=True),
    Column("finished_at", UtcDateTime(), nullable=True),
    Column("report", JSON, nullable=True),
    Column("error", Text, nullable=True),
    Column("trace_id", String(32), nullable=False),
)


class JobStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    async def get_or_create(
        self, *, job_id: str, instrument_id: str, symbol: str, as_of: datetime, trace_id: str
    ) -> Row:
        async with self._engine.begin() as conn:
            existing = (
                (await conn.execute(select(agent_jobs).where(agent_jobs.c.job_id == job_id)))
                .mappings()
                .first()
            )
            if existing is not None:
                return dict(existing)
            # IntegrityError means a concurrent request created this job_id first; fall
            # through and read what it wrote.
            with contextlib.suppress(IntegrityError):
                await conn.execute(
                    agent_jobs.insert().values(
                        job_id=job_id,
                        instrument_id=instrument_id,
                        symbol=symbol,
                        as_of=as_of,
                        status="pending",
                        created_at=utc_now(),
                        started_at=None,
                        finished_at=None,
                        report=None,
                        error=None,
                        trace_id=trace_id,
                    )
                )
            row = (
                (await conn.execute(select(agent_jobs).where(agent_jobs.c.job_id == job_id)))
                .mappings()
                .one()
            )
            return dict(row)

    async def get(self, job_id: str) -> Row | None:
        async with self._engine.connect() as conn:
            row = (
                (await conn.execute(select(agent_jobs).where(agent_jobs.c.job_id == job_id)))
                .mappings()
                .first()
            )
            return dict(row) if row else None

    async def mark_running(self, job_id: str) -> None:
        await self._update(job_id, status="running", started_at=utc_now())

    async def mark_succeeded(self, job_id: str, report: dict[str, Any]) -> None:
        await self._update(job_id, status="succeeded", finished_at=utc_now(), report=report)

    async def mark_failed(self, job_id: str, error: str) -> None:
        await self._update(job_id, status="failed", finished_at=utc_now(), error=error[:2000])

    async def _update(self, job_id: str, **values: Any) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(agent_jobs).where(agent_jobs.c.job_id == job_id).values(**values)
            )


class JobRunner:
    """Executes research jobs with bounded concurrency, one task per job."""

    def __init__(
        self,
        *,
        store: JobStore,
        client: ApiReadClient,
        adapter: LlmAdapter,
        bounds: AgentBounds,
        concurrency: int,
        publish: Publisher,
    ) -> None:
        self._store = store
        self._client = client
        self._adapter = adapter
        self._bounds = bounds
        self._semaphore = asyncio.Semaphore(concurrency)
        self._publish = publish
        self._submitted: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def submit_if_new(
        self, *, job_id: str, instrument: InstrumentRef, name: str, as_of: datetime
    ) -> bool:
        """Start the job unless this process already has (returns whether it started)."""
        if job_id in self._submitted:
            return False
        self._submitted.add(job_id)
        task = asyncio.create_task(
            self._execute(job_id=job_id, instrument=instrument, name=name, as_of=as_of)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def _execute(
        self, *, job_id: str, instrument: InstrumentRef, name: str, as_of: datetime
    ) -> None:
        async with self._semaphore:
            await self._store.mark_running(job_id)
            try:
                report = await run_research(
                    instrument=instrument,
                    name=name,
                    as_of=as_of,
                    job_id=job_id,
                    client=self._client,
                    adapter=self._adapter,
                    bounds=self._bounds,
                )
            except (
                Exception
            ) as exc:  # run_research itself should never raise; this is the last resort
                _log.error("job_execution_failed", job_id=job_id, error=repr(exc))
                await self._store.mark_failed(job_id, repr(exc))
                return
            await self._store.mark_succeeded(job_id, report.model_dump(mode="json"))
            try:
                await self._publish(report)
            except (
                Exception
            ) as exc:  # the job itself succeeded; publishing is best-effort for the read model
                _log.error("report_publish_failed", job_id=job_id, error=repr(exc))

    async def wait_idle(self) -> None:
        """Block until every submitted task has finished. Used by tests and shutdown."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
