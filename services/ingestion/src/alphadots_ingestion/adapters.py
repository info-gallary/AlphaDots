"""Source adapters.

An adapter yields either a valid :class:`RawNewsV1` or a :class:`PoisonRecord`. A bad
record never stops the stream: it is quarantined with enough context to replay it.
"""

from __future__ import annotations

import heapq
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from alphadots_common.ids import derive_id, sha256_hex, trace_id_for
from alphadots_contracts import RawNewsV1, UtcDateTime


@dataclass(frozen=True, slots=True)
class PoisonRecord:
    source: str
    payload_ref: str
    raw: str
    error: str


class SourceAdapter(Protocol):
    name: str

    def records(self) -> Iterator[RawNewsV1 | PoisonRecord]: ...


class MergedAdapter:
    """Interleaves several adapters into one stream ordered by arrival (``first_seen_at``).

    Each input must already be in arrival order, as fixture files are. A poison record
    has no timestamp of its own, so it keeps its place right after the record before it.
    Ties break on adapter position and then on position within the adapter, so the merged
    order is fully deterministic.
    """

    name = "merged"

    def __init__(self, adapters: Sequence[SourceAdapter]) -> None:
        self._adapters = list(adapters)

    def records(self) -> Iterator[RawNewsV1 | PoisonRecord]:
        def keyed(
            index: int, adapter: SourceAdapter
        ) -> Iterator[tuple[datetime, int, int, RawNewsV1 | PoisonRecord]]:
            arrival = datetime.min.replace(tzinfo=UTC)
            for position, record in enumerate(adapter.records()):
                if isinstance(record, RawNewsV1):
                    arrival = record.first_seen_at
                yield arrival, index, position, record

        streams = [keyed(index, adapter) for index, adapter in enumerate(self._adapters)]
        for _, _, _, record in heapq.merge(*streams, key=lambda item: item[:3]):
            yield record


class FixtureNewsItem(BaseModel):
    """One line of a news fixture file. Unknown keys are an error: fixtures are ours."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)
    published_at: UtcDateTime
    first_seen_at: UtcDateTime
    headline: str = Field(min_length=1)
    body: str | None = None
    language: str | None = None
    provider_symbols: tuple[str, ...] = ()
    source_url: str
    source_license: str = Field(min_length=1)


class FixtureReplayAdapter:
    """Replays a JSONL fixture in file order, using the recorded event times.

    Replay is in *event time*: ``first_seen_at`` comes from the fixture, and ``ingested_at``
    is that plus a fixed allowance. Wall-clock time never enters the message, so replaying
    a file yields byte-identical events every time.
    """

    name = "fixture_replay"

    def __init__(
        self, path: Path, *, fixtures_root: Path, ingest_allowance: timedelta = timedelta(seconds=1)
    ) -> None:
        self._path = path
        self._ingest_allowance = ingest_allowance
        try:
            relative = path.resolve().relative_to(fixtures_root.resolve())
        except ValueError as exc:
            raise ValueError(f"{path} is not inside the fixtures root {fixtures_root}") from exc
        self._ref_prefix = f"fixture://{relative.as_posix()}"

    def records(self) -> Iterator[RawNewsV1 | PoisonRecord]:
        with self._path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text or text.startswith("#"):
                    continue
                yield self._parse(text, f"{self._ref_prefix}#L{line_number}")

    def _parse(self, text: str, payload_ref: str) -> RawNewsV1 | PoisonRecord:
        try:
            item = FixtureNewsItem.model_validate(json.loads(text))
            event_id = derive_id("raw", item.source, item.source_event_id)
            ingested_at = item.first_seen_at + self._ingest_allowance
            return RawNewsV1(
                event_id=event_id,
                trace_id=trace_id_for(event_id),
                source=item.source,
                source_event_id=item.source_event_id,
                content_hash=sha256_hex(item.headline, item.body or ""),
                source_url=item.source_url,
                published_at=item.published_at,
                first_seen_at=item.first_seen_at,
                ingested_at=ingested_at,
                available_at=ingested_at,
                source_license=item.source_license,
                payload_ref=payload_ref,
                headline=item.headline,
                body=item.body,
                language=item.language,
                provider_symbols=item.provider_symbols,
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            return PoisonRecord(source=self.name, payload_ref=payload_ref, raw=text, error=str(exc))
