"""FastAPI application: a read-only view over the projected event stream.

Implemented: health, instruments, events, sentiment, signals, overview, dead letters,
research jobs/briefs and monitoring (the last two proxy to ``research_agent``; see
``agent_client.py``). Named by the specification but not built yet: ``/v1/backtests/*``
(M3) and ``/v1/stream`` (M4). They are absent rather than stubbed.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from alphadots_api.agent_client import AgentClient
from alphadots_api.errors import ApiError, error_response
from alphadots_api.read_model import ReadModel
from alphadots_api.routes import ApiContext, health, v1
from alphadots_common.config import Settings
from alphadots_common.logging import get_logger
from alphadots_common.metrics import API_REQUESTS
from alphadots_common.reference import InstrumentMaster

_log = get_logger(__name__)


async def _observe(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Give each request a trace ID and count it by route template and status."""
    request.state.trace_id = uuid.uuid4().hex
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["x-trace-id"] = request.state.trace_id
        return response
    finally:
        route = request.scope.get("route")
        # The route *template*, never the raw path: tickers must not become label values.
        template = getattr(route, "path", "unmatched")
        API_REQUESTS.labels(route=template, status=str(status)).inc()


async def _on_api_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)  # noqa: S101 - registered for ApiError only
    return error_response(request, exc)


async def _on_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    details = [{"location": list(error["loc"]), "message": error["msg"]} for error in exc.errors()]
    return error_response(
        request, ApiError(422, "invalid_parameter", "request validation failed", details)
    )


async def _on_http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    code = "not_found" if exc.status_code == 404 else "http_error"
    return error_response(request, ApiError(exc.status_code, code, str(exc.detail)))


async def _on_unhandled(request: Request, exc: Exception) -> JSONResponse:
    # The detail goes to the log, never to the client.
    _log.error("unhandled_api_error", error=repr(exc), path=request.url.path)
    return error_response(request, ApiError(500, "internal_error", "internal server error"))


def create_app(
    *,
    settings: Settings,
    read_model: ReadModel,
    master: InstrumentMaster,
    agent_client: AgentClient | None = None,
    projection_alive: Callable[[], bool] | None = None,
) -> FastAPI:
    app = FastAPI(
        title="AlphaDots API",
        version="0.1.0",
        summary="Point-in-time research read model. Research only; not investment advice.",
    )
    app.state.context = ApiContext(
        settings=settings,
        read_model=read_model,
        master=master,
        agent_client=agent_client
        or AgentClient(
            settings.research_agent_url, timeout=settings.research_agent_timeout_seconds
        ),
        projection_alive=projection_alive,
    )
    app.middleware("http")(_observe)
    app.add_exception_handler(ApiError, _on_api_error)
    app.add_exception_handler(RequestValidationError, _on_validation_error)
    app.add_exception_handler(StarletteHTTPException, _on_http_error)
    app.add_exception_handler(Exception, _on_unhandled)
    app.include_router(health)
    app.include_router(v1)
    return app
