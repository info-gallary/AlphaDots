"""A polite, caching HTTP client for public data sources.

Every response is stored on disk, keyed by URL, so a study re-runs offline and gives the
same answer. Requests to each host are spaced by a minimum interval. Transient failures
(timeouts, 5xx) are retried with exponential backoff and jitter; a 429 is retried the
same way and, if it persists, stops the download. The client is thread-safe: requests
from any number of threads share one schedule per provider, so concurrency hides network
latency without ever exceeding the provider's rate. There is no attempt to get around a
provider's limits: one declared identity, and a persistent 429 stops the run.
"""

from __future__ import annotations

import gzip
import hashlib
import random
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MISSING_MARKER = b""
"""Cached body of a 404: the source said the resource does not exist."""


class RateLimitedError(RuntimeError):
    """The provider kept refusing requests; stop rather than push harder."""


class CacheMissError(LookupError):
    """Offline mode and the response is not cached."""


class PoliteClient:
    def __init__(
        self,
        cache_dir: Path,
        user_agent: str,
        min_interval: dict[str, float],
        *,
        offline: bool = False,
        timeout: float = 30.0,
        max_attempts: int = 5,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not offline and not user_agent.strip():
            raise ValueError("a User-Agent identifying the requester is required")
        self._cache_dir = cache_dir
        self._min_interval = min_interval
        self._offline = offline
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._clock = clock
        self._next_slot: dict[str, float] = {}
        self._lock = threading.Lock()
        self._http = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self.requests_made = 0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def path_for(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()
        return self._cache_dir / urlsplit(url).netloc / digest[:2] / f"{digest}.gz"

    def cached(self, url: str) -> bool:
        return self.path_for(url).exists()

    def get(self, url: str) -> bytes | None:
        """The body at ``url``, or None if the source says it does not exist (404)."""
        path = self.path_for(url)
        if path.exists():
            body = gzip.decompress(path.read_bytes())
            return None if body == MISSING_MARKER else body
        if self._offline:
            raise CacheMissError(url)
        fetched = self._fetch(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_bytes(gzip.compress(MISSING_MARKER if fetched is None else fetched))
        tmp.replace(path)
        return fetched

    def _wait_turn(self, host: str) -> None:
        # Limits apply per provider domain: www.sec.gov and data.sec.gov share one budget.
        key = next(
            (
                domain
                for domain in self._min_interval
                if host == domain or host.endswith(f".{domain}")
            ),
            host,
        )
        interval = self._min_interval.get(key, 1.0)
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_slot.get(key, now))
            self._next_slot[key] = slot + interval
            self.requests_made += 1
        if slot > now:
            self._sleep(slot - now)

    def _fetch(self, url: str) -> bytes | None:
        host = urlsplit(url).netloc
        for attempt in range(self._max_attempts):
            self._wait_turn(host)
            try:
                response = self._http.get(url)
            except httpx.TransportError:
                if attempt == self._max_attempts - 1:
                    raise
            else:
                if response.status_code == 404:
                    return None
                if response.status_code not in RETRY_STATUS:
                    response.raise_for_status()
                    return response.content
                if response.status_code == 429 and attempt == self._max_attempts - 1:
                    raise RateLimitedError(f"{host} keeps answering 429; stopping")
                if attempt == self._max_attempts - 1:
                    response.raise_for_status()
            self._sleep(min(60.0, 2.0**attempt) + random.uniform(0, 1))  # noqa: S311 - jitter
        raise AssertionError("unreachable")
