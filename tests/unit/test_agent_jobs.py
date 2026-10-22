"""The job store (idempotent creation, status transitions) and the bounded runner.

``JobRunner`` is tested with ``research_graph.run_research`` monkeypatched to a
controllable fake — these tests are about orchestration (dedup, concurrency, publish,
failure handling), not about what a research run itself produces, which
``test_research_agent_graphs.py`` already covers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest

from alphadots_common.db import make_engine
from alphadots_contracts import InstrumentRef, ReportGeneratedV1, ReportValidationStatus
from alphadots_research_agent import jobs as jobs_module
from alphadots_research_agent.bounds import AgentBounds
from alphadots_research_agent.jobs import JobRunner, JobStore

AS_OF = datetime(2026, 3, 2, 23, 0, tzinfo=UTC)
NWSC = InstrumentRef(symbol="NWSC", instrument_id="SYN-0001", confidence=1.0)
BOUNDS = AgentBounds(
    max_tool_calls=8,
    max_tokens=900,
    timeout_seconds=5.0,
    max_correction_attempts=1,
    max_cost_usd=1.0,
)


def _report(job_id: str) -> ReportGeneratedV1:
    return ReportGeneratedV1(
        event_id=f"report_{job_id}",
        trace_id="0" * 32,
        available_at=AS_OF,
        instrument=NWSC,
        as_of=AS_OF,
        job_id=job_id,
        model_id="m",
        model_version="v",
        prompt_version="p",
        validation_status=ReportValidationStatus.UNVALIDATED_AGENT_OUTPUT,
        abstained=False,
        business_snapshot="x",
        quant_summary="x",
        uncertainty_rating="medium",
        tool_calls=0,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=0.0,
    )


@pytest.fixture
async def store() -> JobStore:
    instance = JobStore(make_engine("sqlite+aiosqlite://"))
    await instance.create_schema()
    return instance


# ---------------------------------------------------------------------------- JobStore


async def test_get_or_create_inserts_a_pending_row(store: JobStore) -> None:
    row = await store.get_or_create(
        job_id="j1", instrument_id="SYN-0001", symbol="NWSC", as_of=AS_OF, trace_id="t" * 32
    )
    assert row["status"] == "pending"
    assert row["symbol"] == "NWSC"
    assert row["as_of"] == AS_OF
    assert row["report"] is None
    assert row["error"] is None


async def test_get_or_create_does_not_reset_a_job_that_has_already_advanced(
    store: JobStore,
) -> None:
    await store.get_or_create(
        job_id="j1", instrument_id="SYN-0001", symbol="NWSC", as_of=AS_OF, trace_id="t" * 32
    )
    await store.mark_running("j1")
    await store.mark_succeeded("j1", _report("j1").model_dump(mode="json"))

    again = await store.get_or_create(
        job_id="j1", instrument_id="SYN-0001", symbol="NWSC", as_of=AS_OF, trace_id="t" * 32
    )
    assert again["status"] == "succeeded"
    assert again["report"]["job_id"] == "j1"


async def test_get_returns_none_for_an_unknown_job(store: JobStore) -> None:
    assert await store.get("does-not-exist") is None


async def test_mark_running_sets_status_and_started_at(store: JobStore) -> None:
    await store.get_or_create(
        job_id="j1", instrument_id="SYN-0001", symbol="NWSC", as_of=AS_OF, trace_id="t" * 32
    )
    await store.mark_running("j1")
    row = await store.get("j1")
    assert row is not None
    assert row["status"] == "running"
    assert row["started_at"] is not None


async def test_mark_succeeded_stores_the_report_and_finished_at(store: JobStore) -> None:
    await store.get_or_create(
        job_id="j1", instrument_id="SYN-0001", symbol="NWSC", as_of=AS_OF, trace_id="t" * 32
    )
    await store.mark_succeeded("j1", _report("j1").model_dump(mode="json"))
    row = await store.get("j1")
    assert row is not None
    assert row["status"] == "succeeded"
    assert row["finished_at"] is not None
    assert row["report"]["business_snapshot"] == "x"


async def test_mark_failed_stores_the_error_and_finished_at(store: JobStore) -> None:
    await store.get_or_create(
        job_id="j1", instrument_id="SYN-0001", symbol="NWSC", as_of=AS_OF, trace_id="t" * 32
    )
    await store.mark_failed("j1", "boom: " + "x" * 3000)
    row = await store.get("j1")
    assert row is not None
    assert row["status"] == "failed"
    assert row["error"] is not None
    assert len(row["error"]) <= 2000, "errors are bounded so one bad run cannot bloat the table"


# --------------------------------------------------------------------------- JobRunner


class _FakeRun:
    """Stands in for ``research_graph.run_research``: records calls, can be gated with an
    event to test concurrency, and can be made to raise."""

    def __init__(
        self, *, gate: asyncio.Event | None = None, raises: Exception | None = None
    ) -> None:
        self.calls: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._gate = gate
        self._raises = raises

    async def __call__(
        self,
        *,
        instrument: InstrumentRef,
        name: str,
        as_of: datetime,
        job_id: str,
        client: object,
        adapter: object,
        bounds: AgentBounds,
    ) -> ReportGeneratedV1:
        self.calls.append(job_id)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self._gate is not None:
                await self._gate.wait()
            if self._raises is not None:
                raise self._raises
            return _report(job_id)
        finally:
            self.in_flight -= 1


def _runner(
    store: JobStore,
    fake: _FakeRun,
    *,
    concurrency: int = 2,
    publish: Callable[[ReportGeneratedV1], Awaitable[None]] | None = None,
) -> JobRunner:
    async def default_publish(_report: ReportGeneratedV1) -> None:
        return None

    return JobRunner(
        store=store,
        client=object(),
        adapter=object(),
        bounds=BOUNDS,
        concurrency=concurrency,
        publish=publish or default_publish,
    )  # type: ignore[arg-type]


async def test_submit_if_new_runs_a_job_exactly_once_even_when_called_twice(
    store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRun()
    monkeypatch.setattr(jobs_module, "run_research", fake)
    runner = _runner(store, fake)

    first = runner.submit_if_new(job_id="j1", instrument=NWSC, name="Northwind", as_of=AS_OF)
    second = runner.submit_if_new(job_id="j1", instrument=NWSC, name="Northwind", as_of=AS_OF)
    await runner.wait_idle()

    assert (first, second) == (True, False)
    assert fake.calls == ["j1"]


async def test_a_successful_run_marks_the_job_succeeded_and_publishes(
    store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRun()
    monkeypatch.setattr(jobs_module, "run_research", fake)
    published: list[str] = []

    async def publish(report: ReportGeneratedV1) -> None:
        published.append(report.job_id)

    await store.get_or_create(
        job_id="j1", instrument_id="SYN-0001", symbol="NWSC", as_of=AS_OF, trace_id="t" * 32
    )
    runner = _runner(store, fake, publish=publish)
    runner.submit_if_new(job_id="j1", instrument=NWSC, name="Northwind", as_of=AS_OF)
    await runner.wait_idle()

    row = await store.get("j1")
    assert row is not None
    assert row["status"] == "succeeded"
    assert published == ["j1"]


async def test_an_unexpected_exception_marks_the_job_failed_and_skips_publish(
    store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRun(raises=RuntimeError("unexpected bug"))
    monkeypatch.setattr(jobs_module, "run_research", fake)
    published: list[str] = []

    async def publish(report: ReportGeneratedV1) -> None:
        published.append(report.job_id)

    await store.get_or_create(
        job_id="j1", instrument_id="SYN-0001", symbol="NWSC", as_of=AS_OF, trace_id="t" * 32
    )
    runner = _runner(store, fake, publish=publish)
    runner.submit_if_new(job_id="j1", instrument=NWSC, name="Northwind", as_of=AS_OF)
    await runner.wait_idle()

    row = await store.get("j1")
    assert row is not None
    assert row["status"] == "failed"
    assert "unexpected bug" in row["error"]
    assert published == []


async def test_a_publish_failure_does_not_undo_a_successful_job(
    store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRun()
    monkeypatch.setattr(jobs_module, "run_research", fake)

    async def broken_publish(_report: ReportGeneratedV1) -> None:
        raise ConnectionError("bus is down")

    await store.get_or_create(
        job_id="j1", instrument_id="SYN-0001", symbol="NWSC", as_of=AS_OF, trace_id="t" * 32
    )
    runner = _runner(store, fake, publish=broken_publish)
    runner.submit_if_new(job_id="j1", instrument=NWSC, name="Northwind", as_of=AS_OF)
    await runner.wait_idle()

    row = await store.get("j1")
    assert row is not None
    assert row["status"] == "succeeded", (
        "the job itself completed; a bus outage is not the job's fault"
    )


async def test_concurrency_is_bounded(store: JobStore, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = asyncio.Event()
    fake = _FakeRun(gate=gate)
    monkeypatch.setattr(jobs_module, "run_research", fake)
    runner = _runner(store, fake, concurrency=2)

    for i in range(5):
        runner.submit_if_new(job_id=f"j{i}", instrument=NWSC, name="Northwind", as_of=AS_OF)
    await asyncio.sleep(0.05)  # let every submitted task reach the semaphore/gate
    assert fake.in_flight == 2, "only two of the five jobs should have started"

    gate.set()
    await runner.wait_idle()
    assert sorted(fake.calls) == [f"j{i}" for i in range(5)]
    assert fake.max_in_flight == 2
