"""The consume → validate → handle → publish → commit loop used by every stream stage.

Failure policy:

* A record that is not valid JSON or fails schema validation is quarantined to the
  dead-letter topic immediately and committed. Retrying cannot fix it.
* A handler that raises :class:`RejectMessage` is quarantined immediately for the same reason.
* Any other handler failure is retried with exponential backoff and full jitter. When the
  attempts are exhausted the record is quarantined so one poison record cannot block the
  partition.
* An *infrastructure* failure (a store that is down, as listed in ``infrastructure_errors``)
  is not the record's fault. After the retries it propagates instead of being quarantined:
  the process exits, the record stays uncommitted, and it is redelivered on restart.
* A failure to publish (outputs or the dead letter itself) propagates the same way.
* A dead letter that itself cannot be processed is logged and dropped, never re-queued,
  so the dead-letter topic cannot feed a loop.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from pydantic import ValidationError

from alphadots_common.bus.base import BusConsumer, EventBus, Record
from alphadots_common.ids import derive_id, trace_id_for
from alphadots_common.metrics import DLQ_EVENTS, PIPELINE_LATENCY_SECONDS
from alphadots_common.timeutils import utc_now
from alphadots_contracts import (
    MAX_DLQ_ERROR_CHARS,
    MAX_DLQ_PAYLOAD_CHARS,
    BaseMessage,
    DeadLetterReason,
    DeadLetterV1,
    Topic,
)

_log = structlog.get_logger(__name__)
_TRACE_ID_HEX_CHARS = 32
_POLL_TIMEOUT_SECONDS = 1.0


class RejectMessage(Exception):
    """Raised by a handler for a well-formed message that can never be processed."""


@dataclass(frozen=True, slots=True)
class Outbound:
    topic: Topic
    key: str
    message: BaseMessage


@dataclass(frozen=True, slots=True)
class Route[M: BaseMessage]:
    model: type[M]
    handler: Callable[[M], Awaitable[Sequence[Outbound]]]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.2
    max_delay_seconds: float = 5.0

    def delay(self, attempt: int, rng: random.Random) -> float:
        """Full-jitter backoff before retry number ``attempt`` (1-based)."""
        ceiling = min(self.max_delay_seconds, self.base_delay_seconds * 2 ** (attempt - 1))
        return rng.uniform(0.0, ceiling)


def encode_message(message: BaseMessage) -> bytes:
    return message.model_dump_json().encode("utf-8")


def message_headers(message: BaseMessage) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "schema": type(message).__name__,
        "schema_version": message.schema_version,
        "trace_id": message.trace_id,
    }


async def publish(bus: EventBus, outbound: Outbound) -> None:
    await bus.send(
        outbound.topic.value,
        key=outbound.key,
        value=encode_message(outbound.message),
        headers=message_headers(outbound.message),
    )


class StreamWorker:
    def __init__(
        self,
        *,
        service: str,
        bus: EventBus,
        group: str,
        routes: Mapping[Topic, Route[Any]],
        retry: RetryPolicy | None = None,
        infrastructure_errors: tuple[type[Exception], ...] = (),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = utc_now,
        rng: random.Random | None = None,
    ) -> None:
        self._service = service
        self._bus = bus
        self._group = group
        self._routes = dict(routes)
        self._retry = retry or RetryPolicy()
        self._infrastructure_errors = infrastructure_errors
        self._sleep = sleep
        self._clock = clock
        # Jitter only spreads retries out; it never influences message content.
        self._rng = rng or random.Random()  # noqa: S311
        self._consumer: BusConsumer | None = None

    @property
    def service(self) -> str:
        return self._service

    async def start(self) -> None:
        if self._consumer is None:
            self._consumer = await self._bus.subscribe(
                group=self._group, topics=[topic.value for topic in self._routes]
            )

    async def close(self) -> None:
        if self._consumer is not None:
            await self._consumer.close()
            self._consumer = None

    async def run(self, stop: asyncio.Event) -> None:
        """Process records until ``stop`` is set, then close the consumer cleanly."""
        await self.start()
        try:
            while not stop.is_set():
                consumer = self._require_consumer()
                record = await consumer.poll(_POLL_TIMEOUT_SECONDS)
                if record is not None:
                    await self.process(record)
        finally:
            await self.close()

    async def drain(self) -> int:
        """Process everything currently available and return the record count (for tests)."""
        await self.start()
        processed = 0
        while (record := await self._require_consumer().poll(0)) is not None:
            await self.process(record)
            processed += 1
        return processed

    async def process(self, record: Record) -> None:
        started = time.perf_counter()
        consumer = self._require_consumer()
        route = self._routes[Topic(record.topic)]
        try:
            message = route.model.model_validate_json(record.value)
        except ValidationError as exc:
            invalid_json = any(error["type"] == "json_invalid" for error in exc.errors())
            reason = (
                DeadLetterReason.INVALID_JSON if invalid_json else DeadLetterReason.VALIDATION_ERROR
            )
            await self._dead_letter(record, reason, str(exc))
            await consumer.commit(record)
            return

        with structlog.contextvars.bound_contextvars(
            service=self._service,
            trace_id=message.trace_id,
            event_id=message.event_id,
            topic=record.topic,
        ):
            outputs = await self._handle_with_retry(route, message, record)
            if outputs is not None:
                for outbound in outputs:
                    await publish(self._bus, outbound)
            await consumer.commit(record)
        PIPELINE_LATENCY_SECONDS.labels(stage=self._service).observe(time.perf_counter() - started)

    async def _handle_with_retry(
        self, route: Route[Any], message: BaseMessage, record: Record
    ) -> Sequence[Outbound] | None:
        """Return handler outputs, or ``None`` if the record was quarantined."""
        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                return await route.handler(message)
            except RejectMessage as exc:
                await self._dead_letter(record, DeadLetterReason.VALIDATION_ERROR, str(exc))
                return None
            except Exception as exc:
                if attempt == self._retry.max_attempts:
                    if isinstance(exc, self._infrastructure_errors):
                        _log.error("infrastructure_unavailable", attempts=attempt, error=repr(exc))
                        raise
                    _log.error("handler_failed_permanently", attempts=attempt, error=repr(exc))
                    await self._dead_letter(record, DeadLetterReason.HANDLER_ERROR, repr(exc))
                    return None
                delay = self._retry.delay(attempt, self._rng)
                _log.warning("handler_retry", attempt=attempt, delay=delay, error=repr(exc))
                await self._sleep(delay)
        return None  # unreachable: max_attempts >= 1

    async def _dead_letter(self, record: Record, reason: DeadLetterReason, detail: str) -> None:
        if record.topic == Topic.DEAD_LETTER.value:
            _log.error(
                "dead_letter_unprocessable_dropped",
                service=self._service,
                reason=reason.value,
                offset=record.offset,
                detail=detail[:MAX_DLQ_ERROR_CHARS],
            )
            return
        now = self._clock()
        payload = record.value.decode("utf-8", errors="replace")
        dlq_id = derive_id(
            "dlq", self._service, record.topic, str(record.partition), str(record.offset)
        )
        header_trace = record.headers.get("trace_id", "")
        trace_id = (
            header_trace
            if len(header_trace) == _TRACE_ID_HEX_CHARS
            and all(ch in "0123456789abcdef" for ch in header_trace)
            else trace_id_for(dlq_id)
        )
        message = DeadLetterV1(
            event_id=dlq_id,
            trace_id=trace_id,
            available_at=now,
            service=self._service,
            reason=reason,
            original_topic=record.topic,
            original_partition=record.partition,
            original_offset=record.offset,
            original_key=record.key,
            error_detail=detail[:MAX_DLQ_ERROR_CHARS],
            payload=payload[:MAX_DLQ_PAYLOAD_CHARS],
            payload_truncated=len(payload) > MAX_DLQ_PAYLOAD_CHARS,
            failed_at=now,
        )
        await publish(self._bus, Outbound(Topic.DEAD_LETTER, record.key or dlq_id, message))
        DLQ_EVENTS.labels(service=self._service, reason=reason.value).inc()
        _log.warning(
            "record_dead_lettered",
            service=self._service,
            reason=reason.value,
            topic=record.topic,
            offset=record.offset,
        )

    def _require_consumer(self) -> BusConsumer:
        if self._consumer is None:
            raise RuntimeError("StreamWorker used before start()")
        return self._consumer
