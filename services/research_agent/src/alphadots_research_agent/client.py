"""Read-only, allowlisted access to the AlphaDots API — the agent's only window onto data.

Every call goes through :func:`tracked`, which counts it against the run's bound and
records :data:`~alphadots_common.metrics.AGENT_TOOL_CALLS`. There is no tool here that
writes, places an order, or reaches anything other than the AlphaDots API's own public,
already point-in-time-correct read endpoints — the agent cannot learn a fact the
dashboard itself could not show a person, and it sees exactly what was available as of
the run's ``as_of``, never anything later.

Named by the specification but not implemented, because the data does not exist yet:
``get_filings`` (M1), ``get_market_snapshot`` / ``get_features`` (M3), and
``get_peer_comparison`` (needs sector-relative data, M3). Calling :func:`unavailable` for
one of these raises :class:`ToolUnavailable` rather than returning an empty or
fabricated result, so a brief says "not available" instead of staying silent about it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from alphadots_common.metrics import AGENT_TOOL_CALLS

NOT_YET_AVAILABLE: dict[str, str] = {
    "get_filings": "filings ingestion is not built yet (M1)",
    "get_market_snapshot": "market/price data is not built yet (M3)",
    "get_features": "market features are not built yet (M3)",
    "get_peer_comparison": "sector-relative comparison is not built yet (M3)",
}


class ToolError(Exception):
    """A tool call failed or was refused; it never returns empty or fabricated data."""


class ToolBudgetExceeded(ToolError):
    """The run's tool-call bound was reached."""


class ToolUnavailable(ToolError):
    """Named by the specification but not built yet; see :data:`NOT_YET_AVAILABLE`."""


@dataclass(slots=True)
class ToolBudget:
    """Counts calls for one run and refuses once the bound is reached."""

    max_calls: int
    used: int = 0


def unavailable(tool: str) -> ToolUnavailable:
    AGENT_TOOL_CALLS.labels(tool=tool, status="unavailable").inc()
    return ToolUnavailable(
        f"{tool} is not available: {NOT_YET_AVAILABLE.get(tool, 'not implemented')}"
    )


async def tracked[T](tool: str, budget: ToolBudget, call: Callable[[], Awaitable[T]]) -> T:
    """Spend one unit of ``budget`` on ``tool``, run ``call``, and record the outcome."""
    if budget.used >= budget.max_calls:
        AGENT_TOOL_CALLS.labels(tool=tool, status="budget_exceeded").inc()
        raise ToolBudgetExceeded(f"tool budget of {budget.max_calls} calls exhausted before {tool}")
    budget.used += 1
    try:
        result = await call()
    except ToolError:
        AGENT_TOOL_CALLS.labels(tool=tool, status="error").inc()
        raise
    AGENT_TOOL_CALLS.labels(tool=tool, status="ok").inc()
    return result


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


class ApiReadClient:
    """HTTP client for the subset of the AlphaDots API the agent may call."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None,
        timeout: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._client = client

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"accept": "application/json"}
        if self._token:
            headers["authorization"] = f"Bearer {self._token}"
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient()
        try:
            try:
                response = await client.get(
                    f"{self._base_url}{path}",
                    params={key: value for key, value in params.items() if value is not None},
                    headers=headers,
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                raise ToolError(f"AlphaDots API request to {path} failed: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise ToolError(
                f"AlphaDots API returned {response.status_code} for {path}: {response.text[:300]}"
            )
        result: dict[str, Any] = response.json()
        return result

    async def get_events(
        self, *, symbol: str, as_of: datetime | None, limit: int = 200
    ) -> dict[str, Any]:
        return await self._get(
            "/v1/events", {"symbol": symbol, "as_of": _iso(as_of), "limit": limit}
        )

    async def get_sentiment(
        self, *, symbol: str, as_of: datetime | None, window: str = "14d"
    ) -> dict[str, Any]:
        return await self._get(f"/v1/sentiment/{symbol}", {"as_of": _iso(as_of), "window": window})

    async def get_signal(self, *, symbol: str, as_of: datetime | None) -> dict[str, Any]:
        return await self._get(f"/v1/signals/{symbol}", {"as_of": _iso(as_of)})

    async def get_overview(self, *, as_of: datetime | None = None) -> dict[str, Any]:
        return await self._get("/v1/overview", {"as_of": _iso(as_of)})

    async def get_dead_letters(self, *, limit: int = 50) -> dict[str, Any]:
        return await self._get("/v1/dead-letters", {"limit": limit})
