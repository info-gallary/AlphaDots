"""Download what the earnings-tone study needs into ``var/research`` (never committed).

    uv run python -m scripts.fetch_earnings_data

Resumable: every response is cached, so a re-run only fetches what is missing. SEC
requests carry ``ALPHADOTS_SEC_USER_AGENT`` (the SEC fair-access policy requires a
contact) and stay under 8 per second across all worker threads; other sources get one
request per second.
"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path

from alphadots_research.earnings.events import candidates
from alphadots_research.earnings.http import PoliteClient
from alphadots_research.earnings.sources import (
    FF5_DAILY,
    MOM_DAILY,
    Filing,
    company_tickers,
    daily_bars,
    frames,
    french_daily,
    index_url,
    lm_dictionary,
    parse_press_release_href,
    submissions,
)
from alphadots_research.earnings.universe import FLOAT_PERIODS, select

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "var" / "research"
CACHE = DATA / "cache"
FETCH_START = date(2015, 1, 1)
FETCH_END = date(2025, 12, 31)
SEC_INTERVAL = 0.125
OTHER_INTERVAL = 1.0
WORKERS = 8
"""Concurrent SEC requests; they share the 8-per-second schedule, so this only hides
network latency."""
WEB_AGENT = "AlphaDots research (offline study; one request per second)"


def setting(name: str) -> str:
    """From the environment, else from the repository's gitignored ``.env``."""
    if os.environ.get(name):
        return os.environ[name]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    return ""


def sec_client(*, offline: bool = False) -> PoliteClient:
    agent = setting("ALPHADOTS_SEC_USER_AGENT")
    if not agent and not offline:
        sys.exit("Set ALPHADOTS_SEC_USER_AGENT (name and contact email) in .env first.")
    return PoliteClient(CACHE, agent, {"sec.gov": SEC_INTERVAL}, offline=offline)


def web_client(*, offline: bool = False) -> PoliteClient:
    hosts = (
        "query1.finance.yahoo.com",
        "mba.tuck.dartmouth.edu",
        "drive.google.com",
        "drive.usercontent.google.com",
    )
    return PoliteClient(CACHE, WEB_AGENT, dict.fromkeys(hosts, OTHER_INTERVAL), offline=offline)


def log(message: str) -> None:
    print(f"{datetime.now(UTC):%H:%M:%S} {message}", flush=True)


def main() -> int:
    started = time.monotonic()
    with sec_client() as sec, web_client() as web:
        french_daily(web, FF5_DAILY)
        french_daily(web, MOM_DAILY)
        lm_dictionary(web)
        floats = [
            fact for p in FLOAT_PERIODS for fact in frames(sec, "dei", "EntityPublicFloat", p)
        ]
        assets = [fact for p in FLOAT_PERIODS for fact in frames(sec, "us-gaap", "Assets", p)]
        chosen, rejected = select(floats, assets)
        tickers = company_tickers(sec)
        log(f"universe: {len(chosen)} firms, {len(rejected)} rejected as data errors")

        priced = 0
        for member in chosen:
            ticker = tickers.get(member.cik)
            if ticker and daily_bars(web, ticker) is not None:
                priced += 1
        log(f"prices: {priced} of {len(chosen)} firms have Yahoo history")

        def fetch_release(filing: Filing) -> bool:
            index = sec.get(index_url(filing))
            if index is None:
                return False
            href = parse_press_release_href(index.decode("utf-8", "replace"))
            return href is not None and sec.get(href) is not None

        releases = exhibits = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for n, member in enumerate(chosen, start=1):
                filings = candidates(
                    submissions(sec, member.cik, FETCH_START), FETCH_START, FETCH_END
                )
                releases += len(filings)
                exhibits += sum(pool.map(fetch_release, filings))
                if n % 10 == 0 or n == len(chosen):
                    log(
                        f"filings: {n}/{len(chosen)} firms, {releases} releases, "
                        f"{exhibits} exhibits, {sec.requests_made} SEC requests"
                    )

        manifest = {
            "completed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "universe": len(chosen),
            "rejected": len(rejected),
            "priced": priced,
            "releases": releases,
            "exhibits": exhibits,
            "requests": {"sec": sec.requests_made, "other": web.requests_made},
            "minutes": round((time.monotonic() - started) / 60, 1),
        }
    (DATA / "download-manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    log(f"done: {manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
