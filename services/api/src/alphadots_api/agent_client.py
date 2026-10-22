"""HTTP client to the research_agent service: job creation/status and monitoring.

Every failure to reach the agent becomes ``ApiError(503, "agent_unavailable", ...)`` — the
API never silently returns an empty research result and never runs any LLM logic itself;
that stays entirely inside ``research_agent`` (bounded calls, citation verification,
cost/token tracking — see that service's own module docstring). The ticker the API hands
the agent has already been resolved against the instrument master by the caller, so the
agent itself never has to re-validate it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from alphadots_api.errors import ApiError


class AgentClient:
    def __init__(
        self, base_url: str, *, timeout: float, client: httpx.AsyncClient | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # Tests inject a client wired to an in-process ASGI transport; production leaves
        # this unset and a short-lived real client is opened per call.
        self._client = client

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            try:
                response = await client.request(method, f"{self._base_url}{path}", json=json)
            except httpx.HTTPError as exc:
                raise ApiError(
                    503, "agent_unavailable", f"the research agent is not reachable: {exc}"
                ) from exc
        finally:
            if owns_client:
                await client.aclose()
        if response.status_code == httpx.codes.NOT_FOUND:
            raise ApiError(404, "job_not_found", "no such research job")
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise ApiError(
                503, "agent_unavailable", f"the research agent returned {response.status_code}"
            )
        result: dict[str, Any] = response.json()
        return result

    async def create_job(
        self, *, instrument_id: str, symbol: str, name: str, as_of: datetime | None
    ) -> dict[str, Any]:
        body = {
            "instrument_id": instrument_id,
            "symbol": symbol,
            "name": name,
            "as_of": as_of.isoformat() if as_of else None,
        }
        return await self._request("POST", "/jobs", json=body)

    async def get_job(self, job_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/jobs/{job_id}")

    async def get_monitor(self) -> dict[str, Any]:
        return await self._request("GET", "/monitor")
