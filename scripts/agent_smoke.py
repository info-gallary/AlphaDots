"""Smoke check for the research agent, against a running API + research-agent pair.

    uv run python -m scripts.agent_smoke --base-url http://localhost:18000

Requires the core platform seeded (``make seed``) and the agents profile up
(``make agents``). Works identically whether research-agent is running the deterministic
mock adapter (the default with no Groq key) or a real Groq model — the checks only assert
on the contract (shape, bounds, citations, abstention), never on specific wording, since
a live model's prose is not reproducible and should not be asserted on.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import httpx

TICKER = "NWSC"  # has scored events in the seed fixture; see docs/data-licenses.md
Check = tuple[str, bool, str]


class SmokeFailure(Exception):
    pass


def wait_for_job(client: httpx.Client, job_id: str, deadline: float) -> dict[str, Any]:
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/v1/research/jobs/{job_id}")
        response.raise_for_status()
        last = response.json()["data"]
        if last["status"] in ("succeeded", "failed"):
            return last
        time.sleep(0.5)
    raise SmokeFailure(
        f"job {job_id} did not finish within the deadline (last status: {last.get('status')})"
    )


def run(base_url: str, timeout: float) -> list[Check]:
    client = httpx.Client(base_url=base_url, timeout=10.0)
    deadline = time.monotonic() + timeout
    results: list[Check] = []

    created = client.post("/v1/research/jobs", json={"symbol": TICKER}).json()["data"]
    results.append(
        (
            "job creation returns a pending/running job",
            created["status"] in ("pending", "running"),
            created["status"],
        )
    )

    again = client.post("/v1/research/jobs", json={"symbol": TICKER}).json()["data"]
    results.append(
        (
            "repeating the same request is idempotent (same job_id)",
            again["job_id"] == created["job_id"],
            again["job_id"],
        )
    )

    job = wait_for_job(client, created["job_id"], deadline)
    results.append(
        ("job reaches a terminal state", job["status"] in ("succeeded", "failed"), job["status"])
    )
    results.append(
        (
            "a succeeded job carries research_only=True",
            job["status"] != "succeeded" or job["report"]["research_only"] is True,
            "",
        )
    )
    results.append(
        (
            "every citation traces to a real evidence ID",
            job["status"] != "succeeded"
            or job["report"]["abstained"]
            or all(
                c["evidence_id"] for c in job["report"]["bull_case"] + job["report"]["bear_case"]
            ),
            "",
        )
    )
    results.append(
        (
            "the brief never recommends a trade",
            job["status"] != "succeeded" or _no_recommendation(job["report"]),
            "",
        )
    )

    latest = client.get(f"/v1/research/{TICKER}").json()["data"]
    results.append(
        (
            "the latest-report endpoint now returns it",
            latest is not None and latest["job_id"] == created["job_id"],
            str(latest and latest.get("job_id")),
        )
    )

    monitor = client.get("/v1/monitor").json()["data"]
    results.append(
        (
            "monitoring returns a status and headline",
            monitor["status"] in ("ok", "warning", "critical") and bool(monitor["headline"]),
            monitor["status"],
        )
    )

    missing = client.post("/v1/research/jobs", json={"symbol": "NOPE"})
    results.append(
        (
            "an unknown ticker is rejected before any run starts",
            missing.status_code == 404,
            str(missing.status_code),
        )
    )
    return results


def _no_recommendation(report: dict[str, Any]) -> bool:
    text = " ".join(
        [report["business_snapshot"], report["quant_summary"]]
        + [c["claim"] for c in report["bull_case"] + report["bear_case"]]
    ).lower()
    return not any(
        phrase in text
        for phrase in ("strong buy", "strong sell", "price target", "buy rating", "sell rating")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:18000")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    try:
        results = run(args.base_url, args.timeout)
    except (httpx.HTTPError, SmokeFailure) as exc:
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
