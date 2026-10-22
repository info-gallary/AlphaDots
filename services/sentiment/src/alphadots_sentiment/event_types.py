"""Keyword event typing with a hand-set materiality prior.

The first rule whose keyword appears wins, so order encodes priority. The materiality
values are judgement calls that say "an earnings item usually matters more than a product
note". They have not been estimated from data and must not be read as measured impact.
"""

from __future__ import annotations

from alphadots_contracts import EventType

MATERIALITY_PRIOR_VERSION = "materiality-prior-v0"


def _keywords(words: str) -> frozenset[str]:
    return frozenset(words.split())


_RULES: tuple[tuple[EventType, frozenset[str]], ...] = (
    (EventType.GUIDANCE, _keywords("guidance outlook forecast forecasts expects targets")),
    (
        EventType.EARNINGS,
        _keywords("earnings revenue quarter quarterly eps results margin estimates"),
    ),
    (EventType.M_AND_A, _keywords("acquire acquires acquisition merger takeover buyout stake")),
    (
        EventType.REGULATORY,
        _keywords(
            "regulator regulators regulatory probe investigation lawsuit approval antitrust "
            "subpoena fine fined court settlement"
        ),
    ),
    (
        EventType.MANAGEMENT,
        _keywords("ceo cfo chairman resigns appoints names board executive"),
    ),
    (EventType.PRODUCT, _keywords("launch launches unveils product release recall platform")),
    (EventType.MACRO, _keywords("inflation rates tariff tariffs gdp central economy")),
)

MATERIALITY_PRIOR: dict[EventType, float] = {
    EventType.EARNINGS: 1.0,
    EventType.GUIDANCE: 1.0,
    EventType.M_AND_A: 0.9,
    EventType.REGULATORY: 0.8,
    EventType.MANAGEMENT: 0.6,
    EventType.PRODUCT: 0.5,
    EventType.MACRO: 0.4,
    EventType.OTHER: 0.3,
}


def classify_event_type(tokens: list[str]) -> EventType:
    present = set(tokens)
    for event_type, keywords in _RULES:
        if present & keywords:
            return event_type
    return EventType.OTHER
