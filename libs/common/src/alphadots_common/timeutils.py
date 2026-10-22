"""UTC-only time helpers."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

_WINDOW_RE = re.compile(r"^(?P<count>[1-9]\d{0,3})(?P<unit>[mhd])$")
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86_400}


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` in UTC, refusing naive datetimes instead of guessing a zone."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive datetime is not allowed; attach a timezone explicitly")
    return value.astimezone(UTC)


def parse_window(text: str) -> timedelta:
    """Parse a window such as ``30m``, ``24h`` or ``7d``."""
    match = _WINDOW_RE.match(text.strip().lower())
    if match is None:
        raise ValueError(f"invalid window {text!r}; expected forms like 30m, 24h, 7d")
    return timedelta(seconds=int(match["count"]) * _UNIT_SECONDS[match["unit"]])
