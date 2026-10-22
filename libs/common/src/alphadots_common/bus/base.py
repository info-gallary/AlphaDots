"""Bus abstraction: a minimal Kafka-shaped interface with explicit offset commits.

Delivery is at-least-once. A consumer commits a record only after its effects are durable,
so a crash between effect and commit causes a redelivery. Handlers must therefore be
idempotent, which they are because every output ID is derived from its inputs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Record:
    topic: str
    partition: int
    offset: int
    key: str | None
    value: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


class BusConsumer(Protocol):
    async def poll(self, max_wait: float) -> Record | None:
        """Return the next record, or ``None`` if nothing arrives within ``max_wait`` seconds."""
        ...

    async def commit(self, record: Record) -> None:
        """Mark ``record`` and everything before it on its partition as processed."""
        ...

    async def close(self) -> None: ...


class EventBus(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send(
        self,
        topic: str,
        *,
        key: str,
        value: bytes,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Publish one record and wait until the broker has acknowledged it."""
        ...

    async def subscribe(self, *, group: str, topics: Sequence[str]) -> BusConsumer:
        """Join ``group`` and resume each topic from the group's committed offset."""
        ...
