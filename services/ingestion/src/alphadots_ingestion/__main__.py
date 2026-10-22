"""CLI: ``python -m alphadots_ingestion replay --fixture news/seed.jsonl``.

Pass ``--fixture`` more than once to interleave several files in arrival order.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
from pathlib import Path

from alphadots_common.config import Settings
from alphadots_common.logging import configure_logging, get_logger
from alphadots_common.service import make_bus
from alphadots_ingestion.adapters import FixtureReplayAdapter, MergedAdapter
from alphadots_ingestion.replay import SERVICE, replay


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="alphadots_ingestion")
    commands = parser.add_subparsers(dest="command", required=True)
    replay_cmd = commands.add_parser("replay", help="replay JSONL news fixtures onto the bus")
    replay_cmd.add_argument(
        "--fixture",
        required=True,
        type=Path,
        action="append",
        help="path relative to the fixtures directory; repeatable",
    )
    replay_cmd.add_argument(
        "--speed", type=float, default=0.0, help="event-time acceleration; 0 = no pacing"
    )
    replay_cmd.add_argument("--max-gap-seconds", type=float, default=2.0)
    return parser.parse_args()


def fixture_adapter(settings: Settings, fixtures: list[Path]) -> MergedAdapter:
    return MergedAdapter(
        [
            FixtureReplayAdapter(
                settings.fixtures_dir / fixture,
                fixtures_root=settings.fixtures_dir,
                ingest_allowance=timedelta(seconds=settings.stage_latency_allowance_seconds),
            )
            for fixture in fixtures
        ]
    )


async def _run(args: argparse.Namespace, settings: Settings) -> None:
    log = get_logger(SERVICE)
    bus = make_bus(settings)
    await bus.start()
    try:
        stats = await replay(
            fixture_adapter(settings, args.fixture),
            bus,
            speed=args.speed,
            max_gap_seconds=args.max_gap_seconds,
        )
    finally:
        await bus.stop()
    log.info(
        "replay_finished",
        fixtures=[str(fixture) for fixture in args.fixture],
        published=stats.published,
        quarantined=stats.quarantined,
    )


def main() -> None:
    args = _parse_args()
    settings = Settings(service_name=SERVICE)
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    asyncio.run(_run(args, settings))


if __name__ == "__main__":
    main()
