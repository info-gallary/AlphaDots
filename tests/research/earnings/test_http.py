"""The polite client: caches, retries transient failures, stops on persistent 429s,
and spaces requests per provider domain."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from alphadots_research.earnings.http import CacheMissError, PoliteClient, RateLimitedError


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def client(tmp_path: Path, handler: object, fake: FakeTime, **kwargs: object) -> PoliteClient:
    return PoliteClient(
        tmp_path,
        "test agent test@example.com",
        {"sec.gov": 0.125},
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        sleep=fake.sleep,
        clock=fake.clock,
        **kwargs,  # type: ignore[arg-type]
    )


def test_responses_are_cached_and_404_is_remembered(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/missing":
            return httpx.Response(404)
        return httpx.Response(200, content=b"body")

    fake = FakeTime()
    with client(tmp_path, handler, fake) as http:
        assert http.get("https://www.sec.gov/a") == b"body"
        assert http.get("https://www.sec.gov/a") == b"body"
        assert http.get("https://www.sec.gov/missing") is None
        assert http.get("https://www.sec.gov/missing") is None
    assert len(calls) == 2
    with client(tmp_path, handler, fake, offline=True) as offline:
        assert offline.get("https://www.sec.gov/a") == b"body"
        with pytest.raises(CacheMissError):
            offline.get("https://www.sec.gov/never-fetched")


def test_transient_errors_are_retried_with_backoff(tmp_path: Path) -> None:
    replies = iter([httpx.Response(503), httpx.Response(200, content=b"ok")])
    fake = FakeTime()
    with client(tmp_path, lambda _request: next(replies), fake) as http:
        assert http.get("https://data.sec.gov/x") == b"ok"
    assert any(pause >= 1.0 for pause in fake.slept)


def test_persistent_429_stops_instead_of_pushing_harder(tmp_path: Path) -> None:
    fake = FakeTime()
    with client(tmp_path, lambda _r: httpx.Response(429), fake, max_attempts=3) as http:
        with pytest.raises(RateLimitedError):
            http.get("https://data.sec.gov/y")
        assert http.requests_made == 3


def test_sec_hosts_share_one_request_budget(tmp_path: Path) -> None:
    fake = FakeTime()
    with client(tmp_path, lambda _r: httpx.Response(200, content=b"."), fake) as http:
        http.get("https://data.sec.gov/1")
        http.get("https://www.sec.gov/2")
    assert fake.slept == [pytest.approx(0.125)]


def test_a_user_agent_is_required_to_go_online(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="User-Agent"):
        PoliteClient(tmp_path, " ", {})
