"""``events.dead_letter.v1`` — quarantined messages that could not be processed."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from alphadots_contracts.base import BaseMessage, UtcDateTime

MAX_DLQ_PAYLOAD_CHARS = 65_536
MAX_DLQ_ERROR_CHARS = 2_000


class DeadLetterReason(StrEnum):
    INVALID_JSON = "invalid_json"
    VALIDATION_ERROR = "validation_error"
    HANDLER_ERROR = "handler_error"
    POISON_SOURCE_RECORD = "poison_source_record"


class DeadLetterV1(BaseMessage):
    service: str = Field(min_length=1, max_length=64)
    reason: DeadLetterReason
    original_topic: str = Field(min_length=1, max_length=255)
    original_partition: int | None = None
    original_offset: int | None = None
    original_key: str | None = Field(default=None, max_length=512)
    error_detail: str = Field(max_length=MAX_DLQ_ERROR_CHARS)
    payload: str = Field(
        max_length=MAX_DLQ_PAYLOAD_CHARS,
        description="Original payload decoded as UTF-8 (lossy) and truncated; for replay.",
    )
    payload_truncated: bool = False
    failed_at: UtcDateTime
