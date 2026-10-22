"""Versioned event contracts shared by every AlphaDots service."""

from alphadots_contracts.base import (
    SCHEMA_VERSION,
    BaseMessage,
    ContractModel,
    EventEnvelope,
    InstrumentRef,
    UtcDateTime,
)
from alphadots_contracts.dead_letter import (
    MAX_DLQ_ERROR_CHARS,
    MAX_DLQ_PAYLOAD_CHARS,
    DeadLetterReason,
    DeadLetterV1,
)
from alphadots_contracts.events import (
    AbstainReason,
    EventType,
    NormalizedEventV1,
    PolarityScores,
    RawNewsV1,
    ResolutionFlag,
    SentimentScoredV1,
)
from alphadots_contracts.signals import (
    Horizon,
    RiskFlag,
    SignalAbstainReason,
    SignalContribution,
    SignalCoverage,
    SignalGeneratedV1,
    SignalParams,
    Stance,
    ValidationStatus,
)
from alphadots_contracts.topics import RESERVED_TOPICS, TOPIC_MODELS, Topic, model_for

__all__ = [
    "MAX_DLQ_ERROR_CHARS",
    "MAX_DLQ_PAYLOAD_CHARS",
    "RESERVED_TOPICS",
    "SCHEMA_VERSION",
    "TOPIC_MODELS",
    "AbstainReason",
    "BaseMessage",
    "ContractModel",
    "DeadLetterReason",
    "DeadLetterV1",
    "EventEnvelope",
    "EventType",
    "Horizon",
    "InstrumentRef",
    "NormalizedEventV1",
    "PolarityScores",
    "RawNewsV1",
    "ResolutionFlag",
    "RiskFlag",
    "SentimentScoredV1",
    "SignalAbstainReason",
    "SignalContribution",
    "SignalCoverage",
    "SignalGeneratedV1",
    "SignalParams",
    "Stance",
    "Topic",
    "UtcDateTime",
    "ValidationStatus",
    "model_for",
]
