"""Export the API read model as a static snapshot that the dashboard can serve on its own.

    uv run python -m scripts.export_snapshot           # rewrite the snapshot and goldens
    uv run python -m scripts.export_snapshot --check   # fail if either is stale

Why this exists: the dashboard deploys to Vercel, which cannot run Redpanda, PostgreSQL or
long-lived Kafka consumers. Without a reachable API the dashboard serves this snapshot
instead. The snapshot is the real pipeline's output over real SEC EDGAR filings
(``data/real``, captured by ``scripts/capture_edgar.py``), not hand-written data: the
filings are replayed through the same service code that the containers run, and the
API's read model is dumped as is.

The dashboard answers point-in-time queries over the snapshot in TypeScript
(``web/src/server/snapshot-api.ts``). To keep that honest, this script also records
golden responses from the real FastAPI app for a fixed set of requests, and
``web/test/parity.test.ts`` asserts that the TypeScript answers match them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select

from alphadots_api import tables as api_tables
from alphadots_api.schemas import DeadLetterOut, EventOut
from alphadots_common.config import Settings
from alphadots_common.ids import derive_id
from alphadots_contracts import InstrumentRef
from alphadots_llm import MockAdapter
from alphadots_preprocessor.processor import NORMALIZER_VERSION
from alphadots_research_agent.bounds import AgentBounds
from alphadots_research_agent.client import ApiReadClient
from alphadots_research_agent.monitor_graph import run_monitor
from alphadots_research_agent.research_graph import run_research
from alphadots_sentiment import lexicon
from alphadots_signal_engine import processor as signal_engine
from scripts.local_stack import LocalStack

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "data" / "real"
SNAPSHOT_FILE = ROOT / "web" / "src" / "data" / "snapshot.json"
GOLDEN_FILE = ROOT / "web" / "test" / "parity.golden.json"
FIXTURES = (Path("news/edgar.jsonl"),)
FEED_MODE = "delayed"
"""Real filings replayed from the archive: real data, not a real-time feed."""
SNAPSHOT_VERSION = 1

# Quarantined lines carry no timestamp of their own, so they are stamped with the end of
# the replayed window (the capture ends 2026-10-23) rather than the wall clock. That
# keeps the file byte-stable.
DEAD_LETTER_CLOCK = datetime(2026, 10, 24, 0, 0, tzinfo=UTC)

# Agent output recorded into the snapshot so a deployment without a live API can still show
# real briefs. Each one is a genuine run of the research and monitor graphs (deterministic
# mock model) against this same read model, at the end of the replayed window. Two fields
# depend on the wall clock and would make the file differ on every export, so they are
# fixed explicitly: `available_at`/`generated_at` take the export clock above, and
# `latency_ms` is recorded as null ("not measured here") rather than a made-up number.
AGENT_AS_OF = DEAD_LETTER_CLOCK

# Requests replayed against both implementations. Edge cases are deliberate: point-in-time
# bounds that hide a score, a retired ticker, an ambiguous issuer, pagination, errors.
PARITY_CASES: tuple[str, ...] = (
    "/v1/overview",
    "/v1/overview?as_of=2026-02-01T00:00:00Z",
    "/v1/overview?as_of=2026-07-01T12:00:00Z",
    "/v1/events?limit=200",
    "/v1/events?limit=5",
    "/v1/events?symbol=AAPL&as_of=2026-05-01T00:00:00Z",
    "/v1/events?symbol=INFY",
    "/v1/events?include_duplicates=false&limit=200",
    "/v1/events?as_of=2026-01-29T00:00:00Z&limit=3",
    "/v1/events?as_of=2026-04-30T14:00:00%2B00:00&limit=20",
    "/v1/sentiment/AAPL?window=24h&as_of=2026-04-30T23:00:00Z",
    "/v1/sentiment/MSFT?window=24h",
    "/v1/sentiment/JPM?window=7d",
    "/v1/sentiment/NVDA?window=14d&as_of=2026-08-28T12:00:00Z",
    "/v1/sentiment/HDFCBANK?window=90m&as_of=2026-10-19T12:00:00Z",
    "/v1/signals/AAPL",
    "/v1/signals/MSFT?as_of=2026-04-30T15:00:00Z",
    "/v1/signals/JPM?limit=5",
    "/v1/signals/INFY?as_of=2026-05-01T00:00:00Z",
    "/v1/signals/WIPRO?as_of=2026-07-22T13:30:05Z",
    "/v1/instruments",
    "/v1/instruments?query=bank",
    "/v1/instruments?as_of=2026-05-01T00:00:00Z",
    "/v1/dead-letters",
    "/v1/signals/NOPE",
    "/v1/events?symbol=NOPE",
    "/v1/events/does-not-exist",
    "/v1/nothing-here",
    "/v1/events?as_of=2026-03-02T14:00:00",
    "/v1/events?as_of=yesterday",
    "/v1/events?limit=0",
    "/v1/events?limit=100000",
    "/v1/events?cursor=!!not-base64!!",
    "/v1/sentiment/AAPL?window=soon",
    "/v1/signals/AAPL?horizon=3d",
)


def _strip_volatile(body: Any) -> Any:
    """Remove fields that legitimately differ per request: generation time, trace IDs."""
    if isinstance(body, dict):
        meta = body.get("meta")
        if isinstance(meta, dict):
            meta.pop("generated_at", None)
        error = body.get("error")
        if isinstance(error, dict):
            # Validation details are framework-specific; status and code are the contract.
            return {"error": {"code": error.get("code"), "message": error.get("message")}}
    return body


async def _snapshot(stack: LocalStack) -> dict[str, Any]:
    rows = await stack.read_model.list_events(
        instrument_id=None, as_of=None, limit=1_000_000, cursor=None, include_duplicates=True
    )
    events = [EventOut.from_row(row).model_dump(mode="json") for row in reversed(rows)]
    async with stack.engine.connect() as conn:
        signal_rows = await conn.execute(
            select(api_tables.signals.c.payload).order_by(
                api_tables.signals.c.as_of, api_tables.signals.c.signal_id
            )
        )
        signals = [row[0] for row in signal_rows]
    dead_letters = [
        DeadLetterOut(**row).model_dump(mode="json")
        for row in await stack.read_model.list_dead_letters(10_000)
    ]
    master = stack.master
    manifest = json.loads((FIXTURES_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    used_files = ["instruments.json", *(fixture.as_posix() for fixture in FIXTURES)]
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "description": (
            "AlphaDots API read model and agent briefs, exported from the real pipeline "
            "replaying SEC EDGAR 8-K and 6-K filings (January to October 2026); research "
            "only, not investment advice."
        ),
        "generated_by": "scripts/export_snapshot.py",
        "feed_mode": stack.settings.feed_mode,
        "fixtures": {name: manifest["files"][name]["sha256"] for name in used_files},
        "models": {
            "normalizer": NORMALIZER_VERSION,
            "sentiment": f"{lexicon.MODEL_ID}@{lexicon.MODEL_VERSION}",
            "signal": f"{signal_engine.MODEL_ID}@{signal_engine.MODEL_VERSION}",
        },
        "reference": {
            "version": master.version,
            "license": master.license_name,
            "instruments": [
                {
                    "instrument_id": item.instrument_id,
                    "name": item.name,
                    "sector": item.sector,
                    "aliases": list(item.aliases),
                    "listings": [
                        {
                            "symbol": listing.symbol,
                            "valid_from": listing.valid_from.isoformat(),
                            "valid_to": listing.valid_to.isoformat() if listing.valid_to else None,
                        }
                        for listing in item.listings
                    ],
                }
                for item in master.all()
            ],
        },
        "events": events,
        "signals": signals,
        "dead_letters": dead_letters,
        **await _agent_outputs(stack),
    }


async def _agent_outputs(stack: LocalStack) -> dict[str, Any]:
    """Run the real research and monitor graphs in-process against the exported read model."""
    transport = httpx.ASGITransport(app=stack.app())
    async with httpx.AsyncClient(transport=transport, base_url="http://snapshot") as http:
        client = ApiReadClient("http://snapshot", token=None, timeout=30, client=http)
        adapter = MockAdapter()
        bounds = AgentBounds.from_settings(stack.settings)
        research: dict[str, Any] = {}
        for instrument in stack.master.all():
            symbol = instrument.symbol_on(AGENT_AS_OF.date())
            if symbol is None:
                continue
            report = await run_research(
                instrument=InstrumentRef(
                    symbol=symbol, instrument_id=instrument.instrument_id, confidence=1.0
                ),
                name=instrument.name,
                as_of=AGENT_AS_OF,
                # Same derivation as research_agent's POST /jobs, so IDs match a live run.
                job_id=derive_id("agentjob", instrument.instrument_id, AGENT_AS_OF.isoformat()),
                client=client,
                adapter=adapter,
                bounds=bounds,
            )
            research[symbol] = {
                **report.model_dump(mode="json"),
                "available_at": AGENT_AS_OF.isoformat().replace("+00:00", "Z"),
                "latency_ms": None,
            }
        monitor = await run_monitor(client=client, adapter=adapter, bounds=bounds)
    return {
        "research": research,
        "monitor": {
            **monitor.model_dump(mode="json"),
            "generated_at": AGENT_AS_OF.isoformat().replace("+00:00", "Z"),
            "latency_ms": None,
        },
    }


async def _goldens(stack: LocalStack) -> list[dict[str, Any]]:
    cases = list(PARITY_CASES)
    recorded: list[dict[str, Any]] = []
    transport = httpx.ASGITransport(app=stack.app())
    async with httpx.AsyncClient(transport=transport, base_url="http://parity") as client:
        index = 0
        while index < len(cases):
            path = cases[index]
            response = await client.get(path)
            body = response.json()
            if path == "/v1/events?limit=5":
                # Follow the cursor once, so pagination itself is under test.
                cases.insert(index + 1, f"{path}&cursor={quote(body['page']['next_cursor'])}")
            recorded.append(
                {"path": path, "status": response.status_code, "body": _strip_volatile(body)}
            )
            index += 1
    return recorded


async def build() -> tuple[str, str]:
    """Run the pipeline in-process and return (snapshot JSON, goldens JSON) as text."""
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, bus_backend="memory", fixtures_dir=FIXTURES_DIR, feed_mode=FEED_MODE
    )
    stack = await LocalStack.create(settings)
    try:
        await stack.ingest(*FIXTURES, clock=lambda: DEAD_LETTER_CLOCK)
        await stack.drain()
        snapshot = await _snapshot(stack)
        goldens = await _goldens(stack)
    finally:
        await stack.close()
    return (
        json.dumps(snapshot, indent=1, ensure_ascii=False) + "\n",
        json.dumps({"cases": goldens}, indent=1, ensure_ascii=False) + "\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify instead of writing")
    args = parser.parse_args()

    snapshot_text, golden_text = asyncio.run(build())
    outputs = ((SNAPSHOT_FILE, snapshot_text), (GOLDEN_FILE, golden_text))
    if args.check:
        stale = [
            path
            for path, text in outputs
            if not path.exists() or path.read_text(encoding="utf-8") != text
        ]
        for path in stale:
            print(f"STALE {path.relative_to(ROOT)}: run `make snapshot`", file=sys.stderr)
        return 1 if stale else 0
    for path, text in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(ROOT)} ({len(text.encode('utf-8')) // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
