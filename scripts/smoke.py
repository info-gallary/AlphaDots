"""Smoke check against a running API: is the vertical slice actually working?

    uv run python -m scripts.smoke --base-url http://localhost:18000

Assumes the curated seed fixture has been ingested (``make seed``). Extra data from
``make replay`` is fine: every check targets seed items by their source ID. The script
waits for the pipeline to go quiet by polling, never by sleeping for a guessed duration.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

SEED_EVENTS = 16
SEED_POISON_LINES = 2
BEFORE_FIRST_EVENT = "2026-03-02T13:30:05Z"

Check = tuple[str, bool, str]
Event = dict[str, Any]


class SmokeFailure(Exception):
    pass


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def wait_until[T](
    describe: str, probe: Callable[[], T | None], *, deadline: float, interval: float = 0.5
) -> T:
    """Poll ``probe`` until it returns a value or the deadline passes."""
    last_error = "condition not met"
    while time.monotonic() < deadline:
        try:
            result = probe()
            if result is not None:
                return result
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            last_error = repr(exc)
        time.sleep(interval)
    raise SmokeFailure(f"timed out waiting for {describe} ({last_error})")


class Api:
    def __init__(self, base_url: str, token: str | None) -> None:
        headers = {"authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=10.0)

    def get(self, path: str, **params: Any) -> Any:
        response = self._client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def is_ready(self) -> bool:
        return self._client.get("/health/ready").status_code == 200

    def seed_events(self) -> dict[str, Event]:
        """Seed-fixture events by source ID, found by walking every page."""
        found: dict[str, Event] = {}
        cursor: str | None = None
        while True:
            page = self.get("/v1/events", limit=200, **({"cursor": cursor} if cursor else {}))
            for event in page["data"]:
                if event["source_event_id"].startswith("sw-"):
                    found[event["source_event_id"]] = event
            cursor = page["page"]["next_cursor"]
            if cursor is None:
                return found


def wait_for_pipeline(api: Api, deadline: float) -> dict[str, int]:
    """Block until the seed data has passed every stage and the counts stopped moving."""
    wait_until(
        "the API to report ready", lambda: True if api.is_ready() else None, deadline=deadline
    )
    previous: dict[str, int] = {}

    def settled() -> dict[str, int] | None:
        nonlocal previous
        counts: dict[str, int] = api.get("/v1/overview")["pipeline"]
        events = api.seed_events()
        complete = (
            len(events) >= SEED_EVENTS
            and all(event["sentiment"] is not None for event in events.values())
            and counts["dead_letters"] >= SEED_POISON_LINES
            and counts["signals"] > 0
        )
        stable = counts == previous
        previous = counts
        return counts if complete and stable else None

    return wait_until("the pipeline to settle", settled, deadline=deadline, interval=1.0)


def event_checks(events: dict[str, Event]) -> list[Check]:
    headline = events["sw-a-0001"]
    return [
        (
            "seed events are present exactly once (re-seeding does not duplicate)",
            len(events) == SEED_EVENTS,
            f"{len(events)} of {SEED_EVENTS}",
        ),
        (
            "provenance and availability timestamps are intact",
            headline["payload_ref"] == "fixture://news/seed.jsonl#L4"
            and headline["first_seen_at"] == "2026-03-02T13:30:04Z"
            and headline["available_at"] == "2026-03-02T13:30:06Z"
            and headline["sentiment"]["available_at"] == "2026-03-02T13:30:07Z",
            headline["available_at"],
        ),
        (
            "entity resolution linked the headline to its instrument",
            headline["instruments"]
            == [{"symbol": "NWSC", "instrument_id": "SYN-0001", "confidence": 0.98}],
            str(headline["instruments"]),
        ),
        (
            "cross-provider copy is marked as a duplicate",
            events["sw-b-0001"]["duplicate_of"] == headline["event_id"],
            str(events["sw-b-0001"]["duplicate_of"]),
        ),
        (
            "hostile markup was stripped before storage",
            events["sw-b-0005"]["headline"] == "Vireo Cloud Software posts record revenue",
            events["sw-b-0005"]["headline"],
        ),
        (
            "the sentiment baseline abstains instead of guessing",
            events["sw-a-0006"]["sentiment"]["abstain_reason"] == "no_lexicon_match",
            str(events["sw-a-0006"]["sentiment"]["abstain_reason"]),
        ),
    ]


def research_checks(api: Api) -> list[Check]:
    signal = api.get("/v1/signals/NWSC", as_of="2026-03-02T23:59:59Z")["latest"] or {}
    early = api.get("/v1/overview", as_of=BEFORE_FIRST_EVENT)["pipeline"]
    dead = api.get("/v1/dead-letters", limit=200)["data"]
    poison = [item for item in dead if item["reason"] == "poison_source_record"]
    meta = api.get("/v1/overview")["meta"]
    return [
        (
            "a research-only, explicitly unvalidated signal exists",
            signal.get("research_only") is True
            and signal.get("validation_status") == "unvalidated_heuristic",
            str(signal.get("validation_status")),
        ),
        (
            "every signal input was available at or before the decision time",
            bool(signal)
            and all(
                _parse(item["available_at"]) <= _parse(signal["as_of"])
                for item in signal["contributions"]
            ),
            str(signal.get("as_of")),
        ),
        (
            "point-in-time filter hides everything before the first event",
            early["events"] == early["scored"] == early["signals"] == 0,
            str(early),
        ),
        (
            "poison source lines were quarantined to the dead-letter topic",
            len(poison) == SEED_POISON_LINES,
            f"{len(poison)} of {SEED_POISON_LINES}",
        ),
        (
            "responses are labelled simulated and research-only",
            meta["feed_mode"] == "simulated" and meta["research_only"] is True,
            str(meta["feed_mode"]),
        ),
    ]


def run(base_url: str, timeout: float, token: str | None) -> list[Check]:
    api = Api(base_url, token)
    counts = wait_for_pipeline(api, time.monotonic() + timeout)
    return [
        ("API is ready (database reachable, projection running)", True, ""),
        ("ingest -> normalise -> score -> signal reached the API", True, str(counts)),
        *event_checks(api.seed_events()),
        *research_checks(api),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:18000")
    parser.add_argument("--timeout", type=float, default=90.0, help="seconds to wait in total")
    parser.add_argument("--token", default=None, help="bearer token, if the API requires one")
    args = parser.parse_args()

    try:
        results = run(args.base_url, args.timeout, args.token)
    except SmokeFailure as exc:
        print(f"FAIL  {exc}")
        return 1
    for name, passed, detail in results:
        suffix = f"  [{detail}]" if detail and not passed else ""
        print(f"{'PASS' if passed else 'FAIL'}  {name}{suffix}")
    failed = [name for name, passed, _ in results if not passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed against {args.base_url}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
