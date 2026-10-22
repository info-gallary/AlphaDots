"""In-process bus for tests and the no-Docker dev stack.

It keeps an append-only log per topic and a committed offset per (group, topic), which is
enough to exercise the semantics that matter: a new subscription resumes from the last
commit, so anything processed but not committed is redelivered.

Limits, stated plainly: one partition per topic, no rebalancing, no retention, no
persistence. It is a test double, not a Kafka emulator.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping, Sequence

from alphadots_common.bus.base import BusConsumer, Record


class InMemoryBus:
    def __init__(self) -> None:
        self._logs: dict[str, list[Record]] = defaultdict(list)
        self._committed: dict[tuple[str, str], int] = {}
        self._changed = asyncio.Condition()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(
        self,
        topic: str,
        *,
        key: str,
        value: bytes,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        async with self._changed:
            log = self._logs[topic]
            log.append(
                Record(
                    topic=topic,
                    partition=0,
                    offset=len(log),
                    key=key,
                    value=value,
                    headers=dict(headers or {}),
                )
            )
            self._changed.notify_all()

    async def subscribe(self, *, group: str, topics: Sequence[str]) -> BusConsumer:
        return _InMemoryConsumer(self, group, tuple(topics))

    def records(self, topic: str) -> list[Record]:
        """Snapshot of everything ever published to ``topic`` (test helper)."""
        return list(self._logs[topic])

    def committed(self, group: str, topic: str) -> int:
        return self._committed.get((group, topic), 0)


class _InMemoryConsumer:
    def __init__(self, bus: InMemoryBus, group: str, topics: tuple[str, ...]) -> None:
        self._bus = bus
        self._group = group
        self._topics = topics
        self._positions = {topic: bus.committed(group, topic) for topic in topics}

    def _next_ready(self) -> Record | None:
        for topic in self._topics:
            position = self._positions[topic]
            log = self._bus._logs[topic]
            if position < len(log):
                self._positions[topic] = position + 1
                return log[position]
        return None

    async def poll(self, max_wait: float) -> Record | None:
        async with self._bus._changed:
            record = self._next_ready()
            if record is not None or max_wait <= 0:
                return record
            try:
                async with asyncio.timeout(max_wait):
                    await self._bus._changed.wait_for(self._has_ready)
            except TimeoutError:
                return None
            return self._next_ready()

    def _has_ready(self) -> bool:
        return any(self._positions[t] < len(self._bus._logs[t]) for t in self._topics)

    async def commit(self, record: Record) -> None:
        key = (self._group, record.topic)
        self._bus._committed[key] = max(self._bus._committed.get(key, 0), record.offset + 1)

    async def close(self) -> None:
        return None
