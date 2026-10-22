"""REST surface: shapes, errors, pagination and auth."""

from __future__ import annotations

import httpx
import pytest

from scripts.local_stack import LocalStack
from tests.conftest import api_client, make_settings


async def test_liveness_and_readiness(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health/live")).json() == {"status": "ok"}
    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "checks": {"database": "ok"}}


async def test_every_response_is_self_describing(client: httpx.AsyncClient) -> None:
    for path in ("/v1/overview", "/v1/events", "/v1/instruments", "/v1/signals/NWSC"):
        response = await client.get(path)
        assert response.status_code == 200, path
        meta = response.json()["meta"]
        assert meta["feed_mode"] == "simulated"
        assert meta["research_only"] is True
        assert meta["schema_version"] == "1.0"
        assert "Not investment advice" in meta["disclaimer"]
        assert len(response.headers["x-trace-id"]) == 32


async def test_instrument_search(client: httpx.AsyncClient) -> None:
    body = (await client.get("/v1/instruments", params={"query": "apex"})).json()
    assert [item["symbol"] for item in body["data"]] == ["APXR", "APXE"]
    assert body["reference_version"] == "synthetic-2026.1"
    assert "synthetic" in body["reference_license"]


async def test_instrument_symbols_follow_the_as_of_date(client: httpx.AsyncClient) -> None:
    def vireo(body: dict[str, list[dict[str, str]]]) -> str:
        return next(i["symbol"] for i in body["data"] if i["instrument_id"] == "SYN-0008")

    before = (await client.get("/v1/instruments", params={"as_of": "2026-03-05T00:00:00Z"})).json()
    after = (await client.get("/v1/instruments", params={"as_of": "2026-03-10T00:00:00Z"})).json()
    assert (vireo(before), vireo(after)) == ("VIRE", "VRCS")


async def test_events_filter_by_symbol_including_a_retired_ticker(
    client: httpx.AsyncClient,
) -> None:
    current = (await client.get("/v1/events", params={"symbol": "VRCS"})).json()["data"]
    retired = (await client.get("/v1/events", params={"symbol": "VIRE"})).json()["data"]
    assert len(current) == 3
    assert [e["event_id"] for e in retired] == [e["event_id"] for e in current]
    assert {e["instruments"][0]["symbol"] for e in current} == {"VIRE", "VRCS"}, (
        "each event keeps the ticker that was valid when it was published"
    )


async def test_pagination_walks_every_event_exactly_once(client: httpx.AsyncClient) -> None:
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(20):
        params: dict[str, str | int] = {"limit": 5}
        if cursor:
            params["cursor"] = cursor
        body = (await client.get("/v1/events", params=params)).json()
        seen.extend(event["event_id"] for event in body["data"])
        cursor = body["page"]["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 16
    assert len(set(seen)) == 16

    everything = (await client.get("/v1/events", params={"limit": 200})).json()
    assert [e["event_id"] for e in everything["data"]] == seen, "stable newest-first order"
    assert everything["page"]["next_cursor"] is None


async def test_duplicates_can_be_excluded(client: httpx.AsyncClient) -> None:
    body = (await client.get("/v1/events", params={"include_duplicates": "false"})).json()
    assert len(body["data"]) == 15
    assert all(event["duplicate_of"] is None for event in body["data"])


async def test_event_detail_lists_its_dedup_cluster(client: httpx.AsyncClient) -> None:
    events = (await client.get("/v1/events", params={"symbol": "NWSC"})).json()["data"]
    copy = next(event for event in events if event["duplicate_of"])
    detail = (await client.get(f"/v1/events/{copy['event_id']}")).json()

    assert detail["data"]["event_id"] == copy["event_id"]
    assert [(m["source"], m["is_canonical"]) for m in detail["cluster"]] == [
        ("synthetic_wire_a", True),
        ("synthetic_wire_b", False),
    ]
    assert detail["cluster"][0]["event_id"] == copy["duplicate_of"]


async def test_sentiment_window_summary(client: httpx.AsyncClient) -> None:
    body = (
        await client.get(
            "/v1/sentiment/NWSC", params={"window": "24h", "as_of": "2026-03-02T23:00:00Z"}
        )
    ).json()
    assert body["instrument"]["symbol"] == "NWSC"
    assert body["window_start"] == "2026-03-01T23:00:00Z"
    assert body["summary"] == {
        "events": 3,
        "used": 2,
        "abstained": 0,
        "duplicates": 1,
        "positive": 2,
        "negative": 0,
        "neutral": 0,
        "mean_signed_sentiment": pytest.approx(0.625),
        "mean_entropy": pytest.approx(body["summary"]["mean_entropy"]),
    }
    stamps = [point["available_at"] for point in body["points"]]
    assert stamps == sorted(stamps)


async def test_sentiment_window_defaults_to_the_feed_clock_not_wall_time(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get("/v1/sentiment/VRCS", params={"window": "24h"})).json()
    assert body["meta"]["as_of"] == "2026-03-10T13:00:08Z", "the newest item in the read model"
    assert body["summary"]["events"] == 2


async def test_signals_expose_the_full_contract_payload(client: httpx.AsyncClient) -> None:
    body = (await client.get("/v1/signals/NWSC")).json()
    latest = body["latest"]
    assert latest["validation_status"] == "unvalidated_heuristic"
    assert latest["research_only"] is True
    assert latest["params"]["tau_source"] == "config_prior_unestimated"
    assert latest["instrument"]["instrument_id"] == "SYN-0001"
    assert [point["signal_id"] for point in body["history"]][-1] == latest["event_id"]
    assert len(body["history"]) == 3


async def test_overview_counts_match_the_seed_fixture(client: httpx.AsyncClient) -> None:
    body = (await client.get("/v1/overview")).json()
    assert body["pipeline"] == {
        "events": 16,
        "duplicates": 1,
        "unresolved": 1,
        "scored": 16,
        "sentiment_abstained": 5,
        "signals": 17,
        "signals_abstained": 5,
        "dead_letters": 2,
    }
    assert body["feed"]["earliest_available_at"] == "2026-03-02T13:30:06Z"
    # 8 fictional (SYN-) issuers with fixture events + 6 real NSE issuers carrying zero
    # events by design (instrument-universe reference data only; see instruments.json).
    assert len(body["instruments"]) == 14
    by_symbol = {item["instrument"]["symbol"]: item for item in body["instruments"]}
    assert by_symbol["NWSC"]["event_count"] == 3
    assert by_symbol["HLCB"]["latest_signal"]["stance"] == "negative"


async def test_dead_letters_are_listed(client: httpx.AsyncClient) -> None:
    body = (await client.get("/v1/dead-letters")).json()
    assert {(d["service"], d["reason"]) for d in body["data"]} == {
        ("ingestion", "poison_source_record")
    }
    assert len(body["data"]) == 2


@pytest.mark.parametrize(
    ("path", "params", "status", "code"),
    [
        ("/v1/signals/NOPE", {}, 404, "instrument_not_found"),
        ("/v1/events", {"symbol": "NOPE"}, 404, "instrument_not_found"),
        ("/v1/events/does-not-exist", {}, 404, "event_not_found"),
        ("/v1/nothing-here", {}, 404, "not_found"),
        ("/v1/events", {"as_of": "2026-03-02T14:00:00"}, 422, "invalid_parameter"),
        ("/v1/events", {"as_of": "yesterday"}, 422, "invalid_parameter"),
        ("/v1/events", {"limit": 0}, 422, "invalid_parameter"),
        ("/v1/events", {"limit": 100000}, 422, "invalid_parameter"),
        ("/v1/events", {"cursor": "!!not-base64!!"}, 400, "invalid_cursor"),
        ("/v1/sentiment/NWSC", {"window": "soon"}, 422, "invalid_parameter"),
        ("/v1/signals/NWSC", {"horizon": "3d"}, 422, "invalid_parameter"),
    ],
)
async def test_errors_use_the_standard_error_object(
    client: httpx.AsyncClient, path: str, params: dict[str, str | int], status: int, code: str
) -> None:
    response = await client.get(path, params=params)
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"]
    assert error["trace_id"] == response.headers["x-trace-id"]


async def test_bearer_token_is_enforced_when_configured() -> None:
    stack = await LocalStack.create(make_settings(api_token="local-test-token"))
    try:
        async with api_client(stack) as anonymous:
            denied = await anonymous.get("/v1/overview")
            assert denied.status_code == 401
            assert denied.json()["error"]["code"] == "unauthorized"
            assert (await anonymous.get("/health/live")).status_code == 200, "probes stay open"
        async with api_client(stack, authorization="Bearer wrong") as wrong:
            assert (await wrong.get("/v1/overview")).status_code == 401
        async with api_client(stack, authorization="Bearer local-test-token") as allowed:
            assert (await allowed.get("/v1/overview")).status_code == 200
    finally:
        await stack.close()


async def test_readiness_reports_a_stopped_projection(seeded: LocalStack) -> None:
    from alphadots_api.app import create_app  # noqa: PLC0415

    app = create_app(
        settings=seeded.settings,
        read_model=seeded.read_model,
        master=seeded.master,
        projection_alive=lambda: False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as probe:
        response = await probe.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "projection": "stopped"}


async def test_metrics_label_by_route_template_never_by_ticker(client: httpx.AsyncClient) -> None:
    await client.get("/v1/signals/NWSC")
    text = (await client.get("/metrics")).text
    assert 'route="/v1/signals/{symbol}"' in text
    assert "NWSC" not in text
