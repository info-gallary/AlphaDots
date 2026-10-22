"""Fixture adapter and replay pacing."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from alphadots_common.bus import InMemoryBus
from alphadots_contracts import DeadLetterReason, DeadLetterV1, RawNewsV1, Topic
from alphadots_ingestion.adapters import FixtureReplayAdapter, MergedAdapter, PoisonRecord
from alphadots_ingestion.replay import replay
from tests.conftest import FIXTURES_DIR, SEED

VALID_LINE = (
    '{"source":"w","source_event_id":"%s","published_at":"%s","first_seen_at":"%s",'
    '"headline":"h","source_url":"https://w.example.invalid/x","source_license":"CC0"}'
)


def _adapter(path: Path, root: Path | None = None) -> FixtureReplayAdapter:
    return FixtureReplayAdapter(path, fixtures_root=root or path.parent)


def test_seed_fixture_parses_into_valid_events_and_two_poison_records() -> None:
    records = list(_adapter(FIXTURES_DIR / SEED, FIXTURES_DIR).records())
    events = [item for item in records if isinstance(item, RawNewsV1)]
    poison = [item for item in records if isinstance(item, PoisonRecord)]
    assert (len(events), len(poison)) == (16, 2)

    first = events[0]
    assert first.payload_ref == "fixture://news/seed.jsonl#L4"
    assert first.ingested_at == first.first_seen_at + timedelta(seconds=1)
    assert first.available_at == first.ingested_at
    assert first.instruments == (), "entity resolution is not ingestion's job"
    assert first.provider_symbols == ("NWSC",)

    assert "Unterminated string" in poison[0].error
    assert "timezone-aware" in poison[1].error
    assert all(item.payload_ref.startswith("fixture://news/seed.jsonl#L") for item in poison)


def test_replaying_a_fixture_twice_yields_identical_bytes() -> None:
    def dump() -> list[str]:
        return [
            item.model_dump_json()
            for item in _adapter(FIXTURES_DIR / SEED, FIXTURES_DIR).records()
            if isinstance(item, RawNewsV1)
        ]

    assert dump() == dump()


def test_fixture_outside_the_fixtures_root_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere.jsonl"
    outside.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="not inside the fixtures root"):
        FixtureReplayAdapter(outside, fixtures_root=FIXTURES_DIR)


def test_unknown_fixture_keys_are_poison(tmp_path: Path) -> None:
    path = tmp_path / "f.jsonl"
    line = VALID_LINE % ("a", "2026-03-02T14:00:00Z", "2026-03-02T14:00:01Z")
    path.write_text(line[:-1] + ',"surprise":1}\n', encoding="utf-8")
    (record,) = _adapter(path).records()
    assert isinstance(record, PoisonRecord)
    assert "surprise" in record.error


async def test_poison_records_go_to_the_dead_letter_topic(tmp_path: Path) -> None:
    path = tmp_path / "f.jsonl"
    good = VALID_LINE % ("a", "2026-03-02T14:00:00Z", "2026-03-02T14:00:01Z")
    path.write_text(f"{good}\nnot json at all\n", encoding="utf-8")
    bus = InMemoryBus()

    stats = await replay(_adapter(path), bus)

    assert (stats.published, stats.quarantined) == (1, 1)
    assert len(bus.records(Topic.NEWS_RAW.value)) == 1
    dead = DeadLetterV1.model_validate_json(bus.records(Topic.DEAD_LETTER.value)[0].value)
    assert dead.reason is DeadLetterReason.POISON_SOURCE_RECORD
    assert dead.payload == "not json at all"
    assert dead.original_key == "fixture://f.jsonl#L2"


def test_merged_adapter_interleaves_by_arrival_and_keeps_poison_in_place(tmp_path: Path) -> None:
    def write(name: str, entries: list[tuple[str, str] | str]) -> Path:
        lines = [
            entry if isinstance(entry, str) else VALID_LINE % (entry[0], entry[1], entry[1])
            for entry in entries
        ]
        path = tmp_path / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    first = write(
        "a.jsonl",
        [("a1", "2026-03-02T10:00:00Z"), "poison-after-a1", ("a2", "2026-03-02T12:00:00Z")],
    )
    second = write(
        "b.jsonl",
        [
            ("b1", "2026-03-02T09:00:00Z"),
            ("b2", "2026-03-02T10:00:00Z"),
            ("b3", "2026-03-02T11:00:00Z"),
        ],
    )
    merged = MergedAdapter([_adapter(first, tmp_path), _adapter(second, tmp_path)])

    order = [
        item.source_event_id if isinstance(item, RawNewsV1) else item.raw
        for item in merged.records()
    ]
    assert order == ["b1", "a1", "poison-after-a1", "b2", "b3", "a2"], (
        "ties go to the first adapter, and a poison line stays next to its neighbour"
    )
    assert [item.source_event_id for item in merged.records() if isinstance(item, RawNewsV1)] == [
        "b1",
        "a1",
        "b2",
        "b3",
        "a2",
    ], "merging is repeatable"


async def test_pacing_scales_event_time_gaps_and_caps_them(tmp_path: Path) -> None:
    path = tmp_path / "f.jsonl"
    stamps = ["2026-03-02T14:00:00Z", "2026-03-02T14:00:10Z", "2026-03-02T16:00:10Z"]
    path.write_text(
        "\n".join(VALID_LINE % (str(i), stamp, stamp) for i, stamp in enumerate(stamps)) + "\n",
        encoding="utf-8",
    )
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    await replay(_adapter(path), InMemoryBus(), speed=10.0, max_gap_seconds=2.0, sleep=fake_sleep)
    assert slept == [1.0, 2.0], "10s gap at 10x is 1s; the 2h gap is capped at 2s"

    slept.clear()
    await replay(_adapter(path), InMemoryBus(), speed=0.0, sleep=fake_sleep)
    assert slept == [], "speed 0 disables pacing"

    with pytest.raises(ValueError, match="speed"):
        await replay(_adapter(path), InMemoryBus(), speed=-1.0)
