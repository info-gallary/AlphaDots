"""Deterministic decayed-sentiment heuristic.

Each scored event triggers one signal per instrument: ``S(i, t)`` at
``t = event.available_at``, computed from everything available by ``t``.

What this is: a transparent baseline that summarises recent sentiment.
What this is not: a validated predictor. ``tau``, the stance threshold and the minimum
weight are configuration priors. Nothing here has been fitted or tested against returns,
which is why every signal carries ``validation_status = unvalidated_heuristic``.

Order independence
------------------
A signal is a pure function of the *set* of scored events and their ``available_at``
stamps, never of the order they happened to be processed in. Events can be processed out
of event-time order during a backfill or a multi-file replay. So when an event arrives,
the engine also restates every later signal whose lookback window contains it. A restated
signal has the same deterministic ID and replaces the earlier version downstream.

Consequences worth knowing:

* In-order processing does no extra work: there is nothing later to restate.
* A signal is final once nothing older than it can still arrive. After a replay, read
  signals when the pipeline is quiescent.
* The restated value uses only inputs with ``available_at <= as_of``, so it is the
  point-in-time value, not hindsight. In live operation a late-processed event gets a
  late ``available_at`` and therefore cannot enter an earlier signal at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from alphadots_common.bus.base import EventBus
from alphadots_common.config import Settings
from alphadots_common.db import DB_INFRASTRUCTURE_ERRORS
from alphadots_common.ids import derive_id
from alphadots_common.metrics import MODEL_ABSTENTIONS
from alphadots_common.worker import Outbound, RetryPolicy, Route, StreamWorker
from alphadots_contracts import (
    InstrumentRef,
    RiskFlag,
    SentimentScoredV1,
    SignalAbstainReason,
    SignalContribution,
    SignalCoverage,
    SignalGeneratedV1,
    SignalParams,
    Stance,
    Topic,
    ValidationStatus,
)
from alphadots_quant import SentimentObservation, decayed_sentiment
from alphadots_signal_engine.store import ScoredEventStore, ScoredRow

SERVICE = "signal_engine"
CONSUMER_GROUP = "alphadots.signal_engine.v1"
SIGNAL_KIND = "sentiment_decay_heuristic"
MODEL_ID = "sentiment-decay-heuristic"
MODEL_VERSION = "sdh-0.1.0"
HORIZON = "1d"
MAX_CONTRIBUTIONS = 20
# Below this entity-link confidence the issuer attribution itself is in doubt.
AMBIGUOUS_RELEVANCE = 0.5
LOW_COVERAGE_EVENTS = 2


@dataclass(frozen=True, slots=True)
class SignalConfig:
    tau_seconds: float
    lookback_seconds: float
    stance_threshold: float
    min_effective_weight: float
    stage_allowance: timedelta

    @classmethod
    def from_settings(cls, settings: Settings) -> SignalConfig:
        return cls(
            tau_seconds=settings.signal_tau_seconds,
            lookback_seconds=settings.signal_lookback_seconds,
            stance_threshold=settings.signal_stance_threshold,
            min_effective_weight=settings.signal_min_effective_weight,
            stage_allowance=timedelta(seconds=settings.stage_latency_allowance_seconds),
        )

    @property
    def lookback(self) -> timedelta:
        return timedelta(seconds=self.lookback_seconds)

    def params(self) -> SignalParams:
        return SignalParams(
            tau_seconds=self.tau_seconds,
            lookback_seconds=self.lookback_seconds,
            tau_source="config_prior_unestimated",
            stance_threshold=self.stance_threshold,
            min_effective_weight=self.min_effective_weight,
        )


class SignalEngine:
    def __init__(self, *, store: ScoredEventStore, config: SignalConfig) -> None:
        self._store = store
        self._config = config

    async def handle(self, event: SentimentScoredV1) -> Sequence[Outbound]:
        if not event.instruments:
            return []  # unresolved issuer: nothing tradable to attach a signal to
        await self._store.put(
            [
                ScoredRow(
                    scored_event_id=event.event_id,
                    instrument_id=ref.instrument_id,
                    symbol=ref.symbol,
                    normalized_event_id=event.normalized_event_id,
                    trace_id=event.trace_id,
                    source=event.source,
                    available_at=event.available_at,
                    signed_sentiment=event.signed_sentiment,
                    relevance=ref.confidence,
                    materiality=event.materiality,
                    abstained=event.abstained,
                    is_duplicate=event.novelty == 0.0,
                )
                for ref in event.instruments
            ]
        )
        outputs = []
        for ref in event.instruments:
            # This event's own signal, plus every later signal whose window contains it.
            affected = await self._store.between(
                ref.instrument_id,
                start=event.available_at,
                end=event.available_at + self._config.lookback,
            )
            for trigger in sorted(
                affected, key=lambda row: (row.available_at, row.scored_event_id)
            ):
                signal = await self.compute(trigger)
                outputs.append(Outbound(Topic.SIGNALS_GENERATED, ref.instrument_id, signal))
        return outputs

    async def compute(self, trigger: ScoredRow) -> SignalGeneratedV1:
        """The signal for ``trigger``'s instrument as of ``trigger.available_at``."""
        config = self._config
        as_of = trigger.available_at
        rows = await self._store.between(
            trigger.instrument_id, start=as_of - config.lookback, end=as_of
        )
        duplicates = [row for row in rows if row.is_duplicate]
        abstained = [row for row in rows if row.abstained and not row.is_duplicate]
        usable = [row for row in rows if not row.abstained and not row.is_duplicate]
        by_id = {row.scored_event_id: row for row in usable}

        aggregate = decayed_sentiment(
            (
                SentimentObservation(
                    event_id=row.scored_event_id,
                    available_at=row.available_at,
                    signed_sentiment=row.signed_sentiment,
                    relevance=row.relevance,
                    materiality=row.materiality,
                )
                for row in usable
            ),
            as_of=as_of,
            tau_seconds=config.tau_seconds,
            lookback_seconds=config.lookback_seconds,
        )

        abstain_reason: SignalAbstainReason | None = None
        if not aggregate.contributions:
            abstain_reason = SignalAbstainReason.NO_USABLE_EVENTS
        elif aggregate.effective_weight < config.min_effective_weight:
            abstain_reason = SignalAbstainReason.INSUFFICIENT_EVIDENCE

        stance = Stance.NEUTRAL
        if abstain_reason is None:
            if aggregate.score_normalized >= config.stance_threshold:
                stance = Stance.POSITIVE
            elif aggregate.score_normalized <= -config.stance_threshold:
                stance = Stance.NEGATIVE
        else:
            MODEL_ABSTENTIONS.labels(reason=abstain_reason.value).inc()

        used_rows = [by_id[item.event_id] for item in aggregate.contributions]
        risk_flags: list[RiskFlag] = []
        if 0 < len(used_rows) < LOW_COVERAGE_EVENTS:
            risk_flags.append(RiskFlag.LOW_COVERAGE)
        if any(row.relevance < AMBIGUOUS_RELEVANCE for row in used_rows):
            risk_flags.append(RiskFlag.AMBIGUOUS_ISSUER)
        if used_rows and len({row.source for row in used_rows}) == 1:
            risk_flags.append(RiskFlag.SINGLE_SOURCE)

        # Keep the largest contributions when truncating; ties break on id for determinism.
        ranked = sorted(
            aggregate.contributions, key=lambda item: (-abs(item.contribution), item.event_id)
        )[:MAX_CONTRIBUTIONS]
        ranked.sort(key=lambda item: (item.available_at, item.event_id))

        return SignalGeneratedV1(
            event_id=derive_id(
                "sig", trigger.instrument_id, HORIZON, MODEL_VERSION, trigger.scored_event_id
            ),
            trace_id=trigger.trace_id,
            available_at=as_of + config.stage_allowance,
            instrument=InstrumentRef(
                symbol=trigger.symbol,
                instrument_id=trigger.instrument_id,
                confidence=trigger.relevance,
            ),
            as_of=as_of,
            horizon=HORIZON,
            signal_kind=SIGNAL_KIND,
            model_id=MODEL_ID,
            model_version=MODEL_VERSION,
            validation_status=ValidationStatus.UNVALIDATED_HEURISTIC,
            score=aggregate.score,
            score_normalized=aggregate.score_normalized,
            stance=stance,
            abstained=abstain_reason is not None,
            abstain_reason=abstain_reason,
            params=config.params(),
            coverage=SignalCoverage(
                events_considered=len(rows),
                events_used=len(aggregate.contributions),
                events_abstained=len(abstained),
                duplicates_excluded=len(duplicates),
                effective_weight=aggregate.effective_weight,
            ),
            contributions=tuple(
                SignalContribution(
                    scored_event_id=item.event_id,
                    normalized_event_id=by_id[item.event_id].normalized_event_id,
                    available_at=item.available_at,
                    age_seconds=item.age_seconds,
                    weight=item.weight,
                    signed_sentiment=item.signed_sentiment,
                    contribution=item.contribution,
                )
                for item in ranked
            ),
            contributions_truncated=len(aggregate.contributions) > MAX_CONTRIBUTIONS,
            risk_flags=tuple(risk_flags),
            triggering_event_id=trigger.scored_event_id,
        )


def build_worker(
    *, bus: EventBus, engine: SignalEngine, retry: RetryPolicy | None = None
) -> StreamWorker:
    return StreamWorker(
        service=SERVICE,
        bus=bus,
        group=CONSUMER_GROUP,
        routes={Topic.SENTIMENT_SCORED: Route(SentimentScoredV1, engine.handle)},
        retry=retry,
        infrastructure_errors=DB_INFRASTRUCTURE_ERRORS,
    )
