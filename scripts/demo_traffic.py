"""Drive realistic read traffic through a running stack, so every dashboard has data.

    uv run python -m scripts.demo_traffic --base-url http://localhost:18000

Asks the research agent for a brief on every covered issuer (the NSE issuers have no
news, so those runs abstain), polls each job, reads the monitor, and browses the same
endpoints the dashboard uses. Everything here is an ordinary API call; nothing is
written to the pipeline except the briefs the agent publishes. Part of `make demo`.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

JOB_DEADLINE_SECONDS = 60.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:18000")
    args = parser.parse_args()

    client = httpx.Client(base_url=args.base_url, timeout=15.0)
    overview = client.get("/v1/overview").raise_for_status().json()
    symbols = [item["instrument"]["symbol"] for item in overview["instruments"]]

    for symbol in symbols:
        client.get(f"/v1/sentiment/{symbol}", params={"window": "14d"})
        client.get(f"/v1/signals/{symbol}")
        client.get("/v1/events", params={"symbol": symbol, "limit": 50})
    client.get("/v1/events", params={"limit": 200})
    client.get("/v1/dead-letters")
    # One lookup for a ticker that does not exist: the API answers 404, as it should.
    client.get("/v1/signals/NOPE")

    jobs = {
        symbol: client.post("/v1/research/jobs", json={"symbol": symbol})
        .raise_for_status()
        .json()["data"]
        for symbol in symbols
    }
    deadline = time.monotonic() + JOB_DEADLINE_SECONDS
    outcomes: dict[str, str] = {}
    while jobs and time.monotonic() < deadline:
        for symbol, job in list(jobs.items()):
            current = (
                client.get(f"/v1/research/jobs/{job['job_id']}").raise_for_status().json()["data"]
            )
            if current["status"] in ("succeeded", "failed"):
                report = current.get("report") or {}
                outcomes[symbol] = (
                    "failed"
                    if current["status"] == "failed"
                    else "abstained"
                    if report.get("abstained")
                    else "brief written"
                )
                del jobs[symbol]
        time.sleep(0.5)

    monitor = client.get("/v1/monitor").raise_for_status().json()["data"]
    for symbol in symbols:
        print(f"  {symbol:<10} {outcomes.get(symbol, 'still running')}")
    print(f"monitor: {monitor['status']} - {monitor['headline']}")
    return 1 if jobs else 0


if __name__ == "__main__":
    sys.exit(main())
