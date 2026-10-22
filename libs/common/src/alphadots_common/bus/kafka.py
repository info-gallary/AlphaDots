"""Kafka-protocol bus (Redpanda locally) built on aiokafka.

Offsets are committed manually, per record, after the handler's effects are published.
The producer is idempotent with ``acks=all`` so a retried send cannot duplicate a record
within a producer session; cross-session duplicates are handled by deterministic IDs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition

from alphadots_common.bus.base import BusConsumer, Record


class KafkaBus:
    def __init__(self, bootstrap_servers: str, *, client_id: str) -> None:
        self._bootstrap = bootstrap_servers
        self._client_id = client_id
        self._producer: AIOKafkaProducer | None = None
        self._consumers: list[_KafkaConsumer] = []

    async def start(self) -> None:
        producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap,
            client_id=self._client_id,
            enable_idempotence=True,
            acks="all",
        )
        await producer.start()
        self._producer = producer

    async def stop(self) -> None:
        for consumer in self._consumers:
            await consumer.close()
        self._consumers.clear()
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def send(
        self,
        topic: str,
        *,
        key: str,
        value: bytes,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if self._producer is None:
            raise RuntimeError("KafkaBus.send called before start()")
        await self._producer.send_and_wait(
            topic,
            key=key.encode("utf-8"),
            value=value,
            headers=[(name, text.encode("utf-8")) for name, text in (headers or {}).items()],
        )

    async def subscribe(self, *, group: str, topics: Sequence[str]) -> BusConsumer:
        raw = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self._bootstrap,
            client_id=self._client_id,
            group_id=group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        await raw.start()
        consumer = _KafkaConsumer(raw)
        self._consumers.append(consumer)
        return consumer


class _KafkaConsumer:
    def __init__(self, consumer: AIOKafkaConsumer) -> None:
        self._consumer = consumer
        self._closed = False

    async def poll(self, max_wait: float) -> Record | None:
        batches = await self._consumer.getmany(timeout_ms=int(max_wait * 1000), max_records=1)
        for messages in batches.values():
            for message in messages:
                return Record(
                    topic=message.topic,
                    partition=message.partition,
                    offset=message.offset,
                    key=message.key.decode("utf-8", errors="replace") if message.key else None,
                    value=message.value or b"",
                    headers={
                        name: raw.decode("utf-8", errors="replace")
                        for name, raw in (message.headers or ())
                    },
                )
        return None

    async def commit(self, record: Record) -> None:
        await self._consumer.commit(
            {TopicPartition(record.topic, record.partition): record.offset + 1}
        )

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._consumer.stop()
