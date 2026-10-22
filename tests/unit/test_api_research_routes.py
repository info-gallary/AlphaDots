"""The API's research/monitor routes: ticker validation, proxying, point-in-time reads.

``research_agent`` itself is replaced with a small in-memory fake (``FakeAgentClient``)
injected straight into ``create_app`` — these tests are about what the *API* does around
the agent (validate first, proxy, wrap in the standard envelope, never pass through a
malformed reply), not about the agent's own behaviour, which the ``research_agent`` test
files already cover.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest

from alphadots_api.app import create_app
from alphadots_api.errors import ApiError
from alphadots_api.read_model import ReadModel
from alphadots_common.db import make_engine
from alphadots_common.reference import InstrumentMaster
from alphadots_contracts import ReportGeneratedV1
from tests.conftest import T0, make_settings


class FakeAgentClient:
    """Scripted stand-in for ``AgentClient``: returns canned data or raises ``ApiError``,
    exactly like the real one does when the agent is unreachable or returns an error."""

    def __init__(self) -> None:
        self.create_job_calls: list[dict[str, Any]] = []
        self.get_job_calls: list[str] = []
        self.job_response: dict[str, Any] | Exception = {}
        self.monitor_response: dict[str, Any] | Exception = {}

    async def create_job(
        self, *, instrument_id: str, symbol: str, name: str, as_of: Any
    ) -> dict[str, Any]:
        self.create_job_calls.append(
            {"instrument_id": instrument_id, "symbol": symbol, "name": name, "as_of": as_of}
        )
        if isinstance(self.job_response, Exception):
            raise self.job_response
        return self.job_response

    async def get_job(self, job_id: str) -> dict[str, Any]:
        self.get_job_calls.append(job_id)
        if isinstance(self.job_response, Exception):
            raise self.job_response
        return self.job_response

    async def get_monitor(self) -> dict[str, Any]:
        if isinstance(self.monitor_response, Exception):
            raise self.monitor_response
        return self.monitor_response


def _job_payload(
    job_id: str = "job-1", *, status: str = "pending", report: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "symbol": "NWSC",
        "status": status,
        "as_of": T0.isoformat(),
        "created_at": T0.isoformat(),
        "started_at": None,
        "finished_at": None,
        "report": report,
        "error": None,
    }


def _report_payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "event_id": "report_x",
        "trace_id": "0" * 32,
        "available_at": (T0 + timedelta(seconds=1)).isoformat(),
        "instrument": {"symbol": "NWSC", "instrument_id": "SYN-0001", "confidence": 1.0},
        "as_of": T0.isoformat(),
        "job_id": "job-1",
        "model_id": "mock",
        "model_version": "mock-template-v1",
        "prompt_version": "p",
        "validation_status": "unvalidated_agent_output",
        "abstained": False,
        "business_snapshot": "x",
        "bull_case": [],
        "bear_case": [],
        "quant_summary": "x",
        "missing_information": [],
        "uncertainty_rating": "medium",
        "tool_calls": 1,
        "input_tokens": 1,
        "output_tokens": 1,
        "cost_usd": 0.0,
        "latency_ms": 1.0,
    }
    base.update(overrides)
    return base


@pytest.fixture
async def agent() -> FakeAgentClient:
    return FakeAgentClient()


@pytest.fixture
async def read_model() -> ReadModel:
    instance = ReadModel(make_engine("sqlite+aiosqlite://"))
    await instance.create_schema()
    return instance


@pytest.fixture
async def client(
    agent: FakeAgentClient, read_model: ReadModel, master: InstrumentMaster
) -> httpx.AsyncClient:
    app = create_app(
        settings=make_settings(), read_model=read_model, master=master, agent_client=agent
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# -------------------------------------------------------------- POST /v1/research/jobs


async def test_create_job_rejects_an_unknown_ticker_before_calling_the_agent(
    client: httpx.AsyncClient, agent: FakeAgentClient
) -> None:
    async with client:
        response = await client.post("/v1/research/jobs", json={"symbol": "NOPE"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "instrument_not_found"
    assert agent.create_job_calls == []


async def test_create_job_resolves_the_ticker_and_forwards_instrument_id_and_name(
    client: httpx.AsyncClient, agent: FakeAgentClient
) -> None:
    agent.job_response = _job_payload()
    async with client:
        response = await client.post("/v1/research/jobs", json={"symbol": "NWSC"})
    assert response.status_code == 200
    assert agent.create_job_calls == [
        {
            "instrument_id": "SYN-0001",
            "symbol": "NWSC",
            "name": "Northwind Semiconductor Corp",
            "as_of": None,
        }
    ]
    body = response.json()
    assert body["data"]["job_id"] == "job-1"
    assert body["meta"]["research_only"] is True


async def test_create_job_passes_through_an_explicit_as_of(
    client: httpx.AsyncClient, agent: FakeAgentClient
) -> None:
    agent.job_response = _job_payload()
    async with client:
        await client.post(
            "/v1/research/jobs", json={"symbol": "NWSC", "as_of": "2026-03-02T23:00:00Z"}
        )
    assert agent.create_job_calls[0]["as_of"].isoformat() == "2026-03-02T23:00:00+00:00"


async def test_create_job_rejects_a_naive_as_of(
    client: httpx.AsyncClient, agent: FakeAgentClient
) -> None:
    async with client:
        response = await client.post(
            "/v1/research/jobs", json={"symbol": "NWSC", "as_of": "2026-03-02T23:00:00"}
        )
    assert response.status_code == 422
    assert agent.create_job_calls == []


async def test_create_job_rejects_an_unknown_body_field(client: httpx.AsyncClient) -> None:
    async with client:
        response = await client.post("/v1/research/jobs", json={"symbol": "NWSC", "ticker": "NWSC"})
    assert response.status_code == 422


async def test_create_job_maps_an_unreachable_agent_to_503(
    client: httpx.AsyncClient, agent: FakeAgentClient
) -> None:
    agent.job_response = ApiError(503, "agent_unavailable", "connection refused")
    async with client:
        response = await client.post("/v1/research/jobs", json={"symbol": "NWSC"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "agent_unavailable"


async def test_create_job_rejects_a_malformed_agent_response_as_502(
    client: httpx.AsyncClient, agent: FakeAgentClient
) -> None:
    agent.job_response = {"job_id": "job-1"}  # missing required fields
    async with client:
        response = await client.post("/v1/research/jobs", json={"symbol": "NWSC"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "agent_invalid_response"


# -------------------------------------------------------- GET /v1/research/jobs/{id}


async def test_get_job_proxies_the_agents_response(
    client: httpx.AsyncClient, agent: FakeAgentClient
) -> None:
    report = _report_payload()
    agent.job_response = _job_payload(status="succeeded", report=report)
    async with client:
        response = await client.get("/v1/research/jobs/job-1")
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["status"] == "succeeded"
    assert body["report"]["business_snapshot"] == "x"
    assert agent.get_job_calls == ["job-1"]


async def test_get_job_reports_a_succeeded_abstained_job_honestly(
    client: httpx.AsyncClient, agent: FakeAgentClient
) -> None:
    report = _report_payload(
        abstained=True,
        abstain_reason="insufficient_evidence",
        business_snapshot="Insufficient evidence.",
        quant_summary="Insufficient evidence.",
    )
    agent.job_response = _job_payload(status="succeeded", report=report)
    async with client:
        response = await client.get("/v1/research/jobs/job-1")
    body = response.json()["data"]
    assert body["status"] == "succeeded"
    assert body["report"]["abstained"] is True


async def test_get_job_404_when_the_agent_does_not_know_it(
    client: httpx.AsyncClient, agent: FakeAgentClient
) -> None:
    agent.job_response = ApiError(404, "job_not_found", "no such research job")
    async with client:
        response = await client.get("/v1/research/jobs/does-not-exist")
    assert response.status_code == 404


# ---------------------------------------------------------------- GET /v1/research/{symbol}


async def test_get_latest_research_is_null_before_any_report_exists(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.get("/v1/research/NWSC")
    assert response.status_code == 200
    assert response.json()["data"] is None


async def test_get_latest_research_returns_the_newest_report_visible_as_of(
    client: httpx.AsyncClient, read_model: ReadModel
) -> None:
    older = ReportGeneratedV1.model_validate(
        _report_payload(
            event_id="report_1",
            as_of=T0.isoformat(),
            available_at=(T0 + timedelta(seconds=1)).isoformat(),
        )
    )
    newer = ReportGeneratedV1.model_validate(
        _report_payload(
            event_id="report_2",
            as_of=(T0 + timedelta(hours=1)).isoformat(),
            available_at=(T0 + timedelta(hours=1, seconds=1)).isoformat(),
        )
    )
    await read_model.put_report(older)
    await read_model.put_report(newer)

    async with client:
        latest = await client.get("/v1/research/NWSC")
        assert latest.json()["data"]["event_id"] == "report_2"

        at_older_time = await client.get(
            "/v1/research/NWSC", params={"as_of": (T0 + timedelta(seconds=30)).isoformat()}
        )
        assert at_older_time.json()["data"]["event_id"] == "report_1"

        before_either = await client.get("/v1/research/NWSC", params={"as_of": T0.isoformat()})
        assert before_either.json()["data"] is None


async def test_get_latest_research_rejects_an_unknown_ticker(client: httpx.AsyncClient) -> None:
    async with client:
        response = await client.get("/v1/research/NOPE")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "instrument_not_found"


# ------------------------------------------------------------------------- GET /v1/monitor


async def test_get_monitor_wraps_the_agents_summary(
    client: httpx.AsyncClient, agent: FakeAgentClient
) -> None:
    agent.monitor_response = {
        "cached": True,
        "monitor": {
            "generated_at": T0.isoformat(),
            "status": "ok",
            "headline": "Pipeline looks healthy.",
            "summary": "Nothing to report.",
            "findings": [
                {
                    "severity": "ok",
                    "title": "Healthy",
                    "detail": "",
                    "metric": "status",
                    "value": 0.0,
                }
            ],
            "model_id": "mock",
            "model_version": "mock-template-v1",
            "narration_degraded": False,
            "tool_calls": 1,
            "cost_usd": 0.0,
            "latency_ms": 10.0,
        },
    }
    async with client:
        response = await client.get("/v1/monitor")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "ok"
    assert body["cached"] is True
    assert body["meta"]["research_only"] is True


async def test_get_monitor_rejects_a_malformed_agent_response(
    client: httpx.AsyncClient, agent: FakeAgentClient
) -> None:
    agent.monitor_response = {
        "cached": False,
        "monitor": {"status": "ok"},
    }  # missing required fields
    async with client:
        response = await client.get("/v1/monitor")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "agent_invalid_response"


async def test_get_monitor_maps_an_unreachable_agent_to_503(
    client: httpx.AsyncClient, agent: FakeAgentClient
) -> None:
    agent.monitor_response = ApiError(503, "agent_unavailable", "connection refused")
    async with client:
        response = await client.get("/v1/monitor")
    assert response.status_code == 503
