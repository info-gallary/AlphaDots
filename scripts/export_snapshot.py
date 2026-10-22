"""Export the API read model as a static snapshot that the dashboard can serve on its own.

    uv run python -m scripts.export_snapshot           # rewrite the snapshot and goldens
    uv run python -m scripts.export_snapshot --check   # fail if either is stale

Why this exists: the dashboard deploys to Vercel, which cannot run Redpanda, PostgreSQL or
long-lived Kafka consumers. Without a reachable API the dashboard serves this snapshot
instead. The snapshot is the real pipeline's output over the synthetic fixtures, not
hand-written data: the fixtures are replayed through the same service code that the
containers run, and the API's read model is dumped as is.

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
from alphadots_preprocessor.processor import NORMALIZER_VERSION
from alphadots_sentiment import lexicon
from alphadots_signal_engine import processor as signal_engine
from scripts.local_stack import LocalStack

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "data" / "fixtures"
SNAPSHOT_FILE = ROOT / "web" / "src" / "data" / "snapshot.json"
GOLDEN_FILE = ROOT / "web" / "test" / "parity.golden.json"
FIXTURES = (Path("news/seed.jsonl"), Path("news/replay.jsonl"))
SNAPSHOT_VERSION = 1

# Quarantined fixture lines carry no timestamp of their own, so they are stamped with the
# end of the replayed window rather than the wall clock. That keeps the file byte-stable.
DEAD_LETTER_CLOCK = datetime(2026, 3, 14, 0, 0, tzinfo=UTC)

# Requests replayed against both implementations. Edge cases are deliberate: point-in-time
# bounds that hide a score, a retired ticker, an ambiguous issuer, pagination, errors.
PARITY_CASES: tuple[str, ...] = (
    "/v1/overview",
    "/v1/overview?as_of=2026-03-02T13:30:05Z",
    "/v1/overview?as_of=2026-03-06T12:00:00Z",
    "/v1/events?limit=200",
    "/v1/events?limit=5",
    "/v1/events?symbol=NWSC&as_of=2026-03-05T00:00:00Z",
    "/v1/events?symbol=VIRE",
    "/v1/events?include_duplicates=false&limit=200",
    "/v1/events?as_of=2026-03-02T13:30:06Z&limit=3",
    "/v1/events?as_of=2026-03-09T14:00:00%2B00:00&limit=20",
    "/v1/sentiment/NWSC?window=24h&as_of=2026-03-02T23:00:00Z",
    "/v1/sentiment/VRCS?window=24h",
    "/v1/sentiment/CBHB?window=7d",
    "/v1/sentiment/APXR?window=14d&as_of=2026-03-10T12:00:00Z",
    "/v1/sentiment/HLCB?window=90m&as_of=2026-03-02T15:00:00Z",
    "/v1/signals/NWSC",
    "/v1/signals/APXE?as_of=2026-03-02T15:00:00Z",
    "/v1/signals/HLCB?limit=5",
    "/v1/signals/VIRE?as_of=2026-03-05T00:00:00Z",
    "/v1/signals/LMGU?as_of=2026-03-02T13:30:05Z",
    "/v1/instruments",
    "/v1/instruments?query=apex",
    "/v1/instruments?as_of=2026-03-05T00:00:00Z",
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
    "/v1/sentiment/NWSC?window=soon",
    "/v1/signals/NWSC?horizon=3d",
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
            "AlphaDots API read model, exported from the real pipeline replaying synthetic "
            "fixtures. Simulated data; research only; not investment advice."
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
    settings = Settings(_env_file=None, bus_backend="memory", fixtures_dir=FIXTURES_DIR)  # type: ignore[call-arg]
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
