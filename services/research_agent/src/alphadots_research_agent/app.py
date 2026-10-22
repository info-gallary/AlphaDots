"""FastAPI app for research_agent: job creation/status and on-demand monitoring.

Internal service. The AlphaDots API resolves and validates a ticker against the
instrument master *before* ever calling here (``services/api/.../routes.py``), so this
app trusts the ``instrument_id``/``symbol``/``name`` it is given and only checks shape —
it is not published to the host network by the compose stack (see the ``agents`` profile
in ``infra/compose/docker-compose.yml``).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from alphadots_common.ids import derive_id
from alphadots_common.logging import get_logger
from alphadots_common.timeutils import utc_now
from alphadots_contracts import InstrumentRef
from alphadots_llm import LlmAdapter
from alphadots_research_agent.bounds import AgentBounds
from alphadots_research_agent.client import ApiReadClient
from alphadots_research_agent.jobs import JobRunner, JobStore
from alphadots_research_agent.monitor_graph import run_monitor

_log = get_logger(__name__)
COALESCE_MINUTES = 5


def _coalesced_now() -> datetime:
    """ "as_of omitted" resolves to "now", rounded down to a 5-minute bucket, so repeated
    near-simultaneous requests for "the current brief" land on the same job."""
    now = utc_now()
    floored_minute = (now.minute // COALESCE_MINUTES) * COALESCE_MINUTES
    return now.replace(minute=floored_minute, second=0, microsecond=0)


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: str = Field(min_length=1, max_length=64)
    symbol: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=200)
    as_of: datetime | None = None

    @field_validator("as_of")
    @classmethod
    def _require_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must include a UTC offset")
        return value.astimezone(UTC) if value is not None else None


def _serialize_job(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": row["job_id"],
        "symbol": row["symbol"],
        "status": row["status"],
        "as_of": row["as_of"],
        "created_at": row["created_at"],
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "report": row.get("report"),
        "error": row.get("error"),
    }


def create_app(
    *,
    store: JobStore,
    runner: JobRunner,
    client: ApiReadClient,
    adapter: LlmAdapter,
    bounds: AgentBounds,
    monitor_cache_seconds: float,
) -> FastAPI:
    app = FastAPI(
        title="AlphaDots Research Agent",
        version="0.1.0",
        summary="Internal: bounded LangGraph research and monitoring. Not exposed publicly.",
    )
    monitor_cache: dict[str, Any] = {"payload": None, "fetched_at": None}
    monitor_lock = asyncio.Lock()

    @app.middleware("http")
    async def trace(request: Request, call_next: Any) -> Response:
        request.state.trace_id = uuid.uuid4().hex
        response: Response = await call_next(request)
        response.headers["x-trace-id"] = request.state.trace_id
        return response

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", "unknown")
        details = [
            {"location": list(error["loc"]), "message": error["msg"]} for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_parameter",
                    "message": "request validation failed",
                    "trace_id": trace_id,
                    "details": details,
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", "unknown")
        code = "not_found" if exc.status_code == 404 else "http_error"
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": code,
                    "message": str(exc.detail),
                    "trace_id": trace_id,
                    "details": None,
                }
            },
        )

    @app.exception_handler(Exception)
    async def on_unhandled(request: Request, exc: Exception) -> JSONResponse:
        _log.error("unhandled_agent_error", error=repr(exc), path=request.url.path)
        trace_id = getattr(request.state, "trace_id", "unknown")
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "internal server error",
                    "trace_id": trace_id,
                    "details": None,
                }
            },
        )

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/jobs")
    async def create_job(body: JobCreateRequest) -> dict[str, Any]:
        resolved_as_of = body.as_of or _coalesced_now()
        job_id = derive_id("agentjob", body.instrument_id, resolved_as_of.isoformat())
        row = await store.get_or_create(
            job_id=job_id,
            instrument_id=body.instrument_id,
            symbol=body.symbol,
            as_of=resolved_as_of,
            trace_id=uuid.uuid4().hex,
        )
        instrument = InstrumentRef(
            symbol=body.symbol, instrument_id=body.instrument_id, confidence=1.0
        )
        started = runner.submit_if_new(
            job_id=job_id, instrument=instrument, name=body.name, as_of=resolved_as_of
        )
        _log.info("job_requested", job_id=job_id, symbol=body.symbol, started_new_run=started)
        return _serialize_job(row)

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        row = await store.get(job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such research job")
        return _serialize_job(row)

    @app.get("/monitor")
    async def get_monitor() -> dict[str, Any]:
        now = utc_now()
        fetched_at = monitor_cache["fetched_at"]
        if fetched_at is not None and (now - fetched_at).total_seconds() < monitor_cache_seconds:
            return {"cached": True, "monitor": monitor_cache["payload"]}
        async with monitor_lock:
            # Re-check: another request may have refreshed the cache while we waited.
            now = utc_now()
            fetched_at = monitor_cache["fetched_at"]
            if (
                fetched_at is not None
                and (now - fetched_at).total_seconds() < monitor_cache_seconds
            ):
                return {"cached": True, "monitor": monitor_cache["payload"]}
            result = await run_monitor(client=client, adapter=adapter, bounds=bounds)
            monitor_cache["payload"] = result.model_dump(mode="json")
            monitor_cache["fetched_at"] = utc_now()
            return {"cached": False, "monitor": monitor_cache["payload"]}

    return app
