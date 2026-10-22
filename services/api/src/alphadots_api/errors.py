"""The one error shape every non-2xx response uses."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from alphadots_api import schemas as s


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def error_response(request: Request, error: ApiError) -> JSONResponse:
    body = s.ErrorResponse(
        error=s.ErrorBody(
            code=error.code,
            message=error.message,
            trace_id=getattr(request.state, "trace_id", "unknown"),
            details=error.details,
        )
    )
    return JSONResponse(status_code=error.status_code, content=body.model_dump(mode="json"))
