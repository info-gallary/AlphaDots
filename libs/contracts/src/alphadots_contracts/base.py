"""Shared building blocks for every versioned AlphaDots message.

Compatibility policy (enforced by ``tests/contract``):

* A topic name carries the **major** version (``news.raw.v1``). A breaking change means a
  new topic, never an in-place edit.
* Within a major version, changes are additive only: new fields must have defaults, and
  consumers ignore fields they do not know (``extra="ignore"``).
* Every timestamp is timezone-aware and normalised to UTC. Naive datetimes are rejected,
  because a silently assumed timezone is a look-ahead bug waiting to happen.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0"


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware (naive datetimes are rejected)")
    return value.astimezone(UTC)


UtcDateTime = Annotated[datetime, AfterValidator(_require_utc)]
UnitInterval = Annotated[float, Field(ge=0.0, le=1.0)]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ContractModel(BaseModel):
    """Immutable value object; unknown fields are ignored for forward compatibility."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class InstrumentRef(ContractModel):
    """A resolved link between an event and a tradable instrument.

    ``instrument_id`` is the stable identifier; ``symbol`` is the ticker that was effective
    at the event's publication time and may be reused by a different issuer later.
    """

    symbol: str = Field(min_length=1, max_length=16)
    instrument_id: str = Field(min_length=1, max_length=64)
    confidence: UnitInterval


class BaseMessage(ContractModel):
    """Fields common to every message on the bus."""

    schema_version: str = Field(default=SCHEMA_VERSION, pattern=r"^1\.\d+$")
    event_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    available_at: UtcDateTime = Field(
        description="Earliest time a consumer could realistically have used this message. "
        "Every point-in-time filter compares against this field."
    )


class EventEnvelope(BaseMessage):
    """Provenance envelope carried by every source-derived event."""

    source: str = Field(min_length=1, max_length=64)
    source_event_id: str = Field(min_length=1, max_length=256)
    content_hash: Sha256Hex
    source_url: str = Field(min_length=1, max_length=2048, pattern=r"^https?://")
    published_at: UtcDateTime = Field(description="Publication time as claimed by the source.")
    first_seen_at: UtcDateTime = Field(description="When AlphaDots first observed the item.")
    ingested_at: UtcDateTime = Field(description="When the item was accepted into the pipeline.")
    instruments: tuple[InstrumentRef, ...] = ()
    source_license: str = Field(min_length=1, max_length=128)
    payload_ref: str = Field(
        min_length=1,
        max_length=1024,
        description="URI of the immutable raw payload (fixture://, s3://, ...).",
    )

    @model_validator(mode="after")
    def _availability_is_monotonic(self) -> Self:
        # `published_at` is a source claim and may be skewed, so it is deliberately not
        # constrained. What we observed and when we could act on it must be ordered.
        if not self.first_seen_at <= self.ingested_at <= self.available_at:
            raise ValueError(
                "expected first_seen_at <= ingested_at <= available_at, got "
                f"{self.first_seen_at.isoformat()} / {self.ingested_at.isoformat()} / "
                f"{self.available_at.isoformat()}"
            )
        return self
