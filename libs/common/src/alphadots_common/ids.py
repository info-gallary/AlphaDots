"""Deterministic identifiers.

Every derived ID is a pure function of its inputs, so reprocessing the same message always
yields the same output message. That is what makes at-least-once delivery safe: a
redelivered record becomes an idempotent upsert downstream instead of a duplicate.
"""

from __future__ import annotations

import hashlib

_ID_HEX_CHARS = 32


def sha256_hex(*parts: str) -> str:
    """Hash ``parts`` with length-prefixing so ("ab", "c") never collides with ("a", "bc")."""
    digest = hashlib.sha256()
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def derive_id(namespace: str, *parts: str) -> str:
    """Return a stable ``<namespace>_<32 hex>`` identifier for ``parts``."""
    return f"{namespace}_{sha256_hex(namespace, *parts)[:_ID_HEX_CHARS]}"


def trace_id_for(event_id: str) -> str:
    """Trace ID for the processing of a source event.

    In replay mode the trace ID is derived from the event so that a replay produces
    byte-identical messages. Live ingestion (M1) will mint a random ID per observation.
    """
    return sha256_hex("trace", event_id)[:_ID_HEX_CHARS]
