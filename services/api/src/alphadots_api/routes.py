"""Route handlers. Each one resolves ``as_of`` first and passes it to every read."""

from __future__ import annotations

import base64
import binascii
import hmac
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from alphadots_api import schemas as s
from alphadots_api.errors import ApiError
from alphadots_api.read_model import ReadModel
from alphadots_common.config import Settings
from alphadots_common.logging import get_logger
from alphadots_common.reference import Instrument, InstrumentMaster
from alphadots_common.timeutils import parse_window, utc_now
from alphadots_contracts import Horizon, SignalGeneratedV1

_log = get_logger(__name__)

MAX_PAGE_SIZE = 200
MAX_SENTIMENT_POINTS = 500
MAX_SIGNAL_HISTORY = 500
OVERVIEW_SIGNAL_SCAN = 5000
OVERVIEW_HISTORY_POINTS = 60


@dataclass(frozen=True, slots=True)
class ApiContext:
    """Everything a handler needs; attached to the app at construction."""

    settings: Settings
    read_model: ReadModel
    master: InstrumentMaster
    projection_alive: Callable[[], bool] | None = None

    def meta(self, as_of: datetime | None) -> s.Meta:
        return s.Meta(generated_at=utc_now(), as_of=as_of, feed_mode=self.settings.feed_mode)

    async def reference_date(self, as_of: datetime | None) -> date:
        """The date used to interpret tickers: as_of, else the feed's latest event time."""
        if as_of is not None:
            return as_of.date()
        _, latest = await self.read_model.feed_bounds()
        return (latest or utc_now()).date()

    async def resolve_symbol(self, symbol: str, as_of: datetime | None) -> tuple[Instrument, date]:
        on = await self.reference_date(as_of)
        instrument = self.master.by_symbol(symbol, on)
        if instrument is None:
            # Fall back to tickers that were valid at some other time, if unambiguous.
            historical = self.master.ever_listed_as(symbol)
            if len(historical) == 1:
                instrument = historical[0]
        if instrument is None:
            raise ApiError(404, "instrument_not_found", f"no instrument is known as {symbol!r}")
        return instrument, on


def get_context(request: Request) -> ApiContext:
    context: ApiContext = request.app.state.context
    return context


def require_token(request: Request) -> None:
    expected = get_context(request).settings.api_token
    if expected is None:
        return
    scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
    valid = scheme.lower() == "bearer" and hmac.compare_digest(
        supplied.encode("utf-8"), expected.get_secret_value().encode("utf-8")
    )
    if not valid:
        raise ApiError(401, "unauthorized", "missing or invalid bearer token")


def bounded_as_of(
    as_of: Annotated[
        datetime | None,
        Query(description="Point-in-time bound (ISO 8601 with offset). Omit for latest."),
    ] = None,
) -> datetime | None:
    if as_of is None:
        return None
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ApiError(
            422,
            "invalid_parameter",
            "as_of must include a UTC offset, for example 2026-03-05T14:00:00Z",
        )
    return as_of.astimezone(UTC)


Context = Annotated[ApiContext, Depends(get_context)]
AsOf = Annotated[datetime | None, Depends(bounded_as_of)]
PageLimit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


def _encode_cursor(available_at: datetime, event_id: str) -> str:
    raw = f"{available_at.isoformat()}|{event_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        stamp, _, event_id = decoded.partition("|")
        available_at = datetime.fromisoformat(stamp)
        if not event_id or available_at.tzinfo is None:
            raise ValueError("incomplete cursor")
    except (ValueError, binascii.Error, UnicodeError) as exc:
        raise ApiError(400, "invalid_cursor", "cursor is not valid; restart pagination") from exc
    return available_at, event_id


def _instrument_out(instrument: Instrument, on: date) -> s.InstrumentOut:
    return s.InstrumentOut(
        instrument_id=instrument.instrument_id,
        symbol=instrument.symbol_on(on) or instrument.all_symbols[-1],
        name=instrument.name,
        sector=instrument.sector,
        all_symbols=list(instrument.all_symbols),
    )


def _signal_point(row: Mapping[str, Any]) -> s.SignalPointOut:
    return s.SignalPointOut(
        signal_id=row["signal_id"],
        as_of=row["as_of"],
        available_at=row["available_at"],
        score=row["score"],
        score_normalized=row["score_normalized"],
        stance=row["stance"],
        abstained=row["abstained"],
    )


def _sentiment_point(row: Mapping[str, Any]) -> s.SentimentPointOut:
    return s.SentimentPointOut(
        event_id=row["event_id"],
        headline=row["headline"],
        source=row["source"],
        published_at=row["published_at"],
        available_at=row["s_available_at"],
        signed_sentiment=row["s_signed_sentiment"],
        entropy=row["s_entropy"],
        abstained=row["s_abstained"],
        abstain_reason=row["s_abstain_reason"],
        is_duplicate=row["duplicate_of"] is not None,
        event_type=row["s_event_type"],
        materiality=row["s_materiality"],
        relevance=row["relevance"],
    )


def _summarise(points: list[s.SentimentPointOut]) -> s.SentimentSummaryOut:
    used = [p for p in points if not p.abstained and not p.is_duplicate]
    return s.SentimentSummaryOut(
        events=len(points),
        used=len(used),
        abstained=sum(1 for p in points if p.abstained and not p.is_duplicate),
        duplicates=sum(1 for p in points if p.is_duplicate),
        positive=sum(1 for p in used if p.signed_sentiment > 0),
        negative=sum(1 for p in used if p.signed_sentiment < 0),
        neutral=sum(1 for p in used if p.signed_sentiment == 0),
        mean_signed_sentiment=sum(p.signed_sentiment for p in used) / len(used) if used else None,
        mean_entropy=sum(p.entropy for p in used) / len(used) if used else None,
    )


# ------------------------------------------------------------------- health

health = APIRouter()


@health.get("/health/live", tags=["health"])
async def live() -> dict[str, str]:
    return {"status": "ok"}


@health.get("/health/ready", tags=["health"])
async def ready(ctx: Context) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        await ctx.read_model.ping()
        checks["database"] = "ok"
    except Exception as exc:
        _log.warning("readiness_database_failed", error=repr(exc))
        checks["database"] = "unavailable"
    if ctx.projection_alive is not None:
        checks["projection"] = "ok" if ctx.projection_alive() else "stopped"
    is_ready = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={"status": "ready" if is_ready else "not_ready", "checks": checks},
    )


@health.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ----------------------------------------------------------------------- v1

v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])


@v1.get("/instruments", response_model=s.InstrumentsResponse, tags=["reference"])
async def list_instruments(
    ctx: Context, as_of: AsOf, query: Annotated[str, Query(max_length=64)] = ""
) -> s.InstrumentsResponse:
    on = await ctx.reference_date(as_of)
    return s.InstrumentsResponse(
        data=[_instrument_out(item, on) for item in ctx.master.search(query, on)],
        reference_version=ctx.master.version,
        reference_license=ctx.master.license_name,
        meta=ctx.meta(as_of),
    )


@v1.get("/events", response_model=s.EventsResponse, tags=["events"])
async def list_events(
    ctx: Context,
    as_of: AsOf,
    *,
    symbol: Annotated[str | None, Query(max_length=16)] = None,
    limit: PageLimit = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    include_duplicates: bool = True,
) -> s.EventsResponse:
    instrument_id = (await ctx.resolve_symbol(symbol, as_of))[0].instrument_id if symbol else None
    rows = await ctx.read_model.list_events(
        instrument_id=instrument_id,
        as_of=as_of,
        limit=limit + 1,  # one extra row tells us whether another page exists
        cursor=_decode_cursor(cursor) if cursor else None,
        include_duplicates=include_duplicates,
    )
    page_rows = rows[:limit]
    next_cursor = (
        _encode_cursor(page_rows[-1]["available_at"], page_rows[-1]["event_id"])
        if len(rows) > limit
        else None
    )
    return s.EventsResponse(
        data=[s.EventOut.from_row(row) for row in page_rows],
        page=s.Page(limit=limit, next_cursor=next_cursor),
        meta=ctx.meta(as_of),
    )


@v1.get("/events/{event_id}", response_model=s.EventDetailResponse, tags=["events"])
async def get_event(ctx: Context, as_of: AsOf, event_id: str) -> s.EventDetailResponse:
    row = await ctx.read_model.get_event(event_id, as_of)
    if row is None:
        raise ApiError(404, "event_not_found", "no such event is visible as of this time")
    members = await ctx.read_model.cluster_members(row["cluster_id"], as_of)
    return s.EventDetailResponse(
        data=s.EventOut.from_row(row),
        cluster=[
            s.ClusterMemberOut(
                event_id=member["event_id"],
                source=member["source"],
                source_event_id=member["source_event_id"],
                first_seen_at=member["first_seen_at"],
                available_at=member["available_at"],
                is_canonical=member["duplicate_of"] is None,
            )
            for member in members
        ],
        meta=ctx.meta(as_of),
    )


@v1.get("/sentiment/{symbol}", response_model=s.SentimentResponse, tags=["sentiment"])
async def get_sentiment(
    ctx: Context,
    as_of: AsOf,
    symbol: str,
    window: Annotated[str, Query(max_length=8, description="e.g. 24h, 7d")] = "24h",
) -> s.SentimentResponse:
    try:
        span = parse_window(window)
    except ValueError as exc:
        raise ApiError(422, "invalid_parameter", str(exc)) from exc
    instrument, on = await ctx.resolve_symbol(symbol, as_of)
    # With no as_of the window ends at the newest data held, not at wall-clock now:
    # a replayed feed's "now" is its own latest event time.
    end = as_of or (await ctx.read_model.feed_bounds())[1] or utc_now()
    start = end - span
    rows = await ctx.read_model.sentiment_points(
        instrument.instrument_id, start=start, as_of=end, limit=MAX_SENTIMENT_POINTS + 1
    )
    points = [_sentiment_point(row) for row in rows[-MAX_SENTIMENT_POINTS:]]
    return s.SentimentResponse(
        instrument=_instrument_out(instrument, on),
        window=window,
        window_start=start,
        summary=_summarise(points),
        points=points,
        points_truncated=len(rows) > MAX_SENTIMENT_POINTS,
        meta=ctx.meta(end),
    )


@v1.get("/signals/{symbol}", response_model=s.SignalsResponse, tags=["signals"])
async def get_signals(
    ctx: Context,
    as_of: AsOf,
    symbol: str,
    horizon: Horizon = "1d",
    limit: Annotated[int, Query(ge=1, le=MAX_SIGNAL_HISTORY)] = 200,
) -> s.SignalsResponse:
    instrument, on = await ctx.resolve_symbol(symbol, as_of)
    rows = await ctx.read_model.signal_history(
        instrument.instrument_id, horizon, as_of=as_of, limit=limit
    )
    return s.SignalsResponse(
        instrument=_instrument_out(instrument, on),
        horizon=horizon,
        latest=SignalGeneratedV1.model_validate(rows[-1]["payload"]) if rows else None,
        history=[_signal_point(row) for row in rows],
        meta=ctx.meta(as_of),
    )


@v1.get("/overview", response_model=s.OverviewResponse, tags=["overview"])
async def get_overview(ctx: Context, as_of: AsOf) -> s.OverviewResponse:
    on = await ctx.reference_date(as_of)
    earliest, latest = await ctx.read_model.feed_bounds()
    counts = await ctx.read_model.pipeline_counts(as_of)
    event_counts = await ctx.read_model.instrument_event_counts(as_of)
    history: dict[str, list[s.SignalPointOut]] = defaultdict(list)
    for row in await ctx.read_model.recent_signals(as_of, OVERVIEW_SIGNAL_SCAN):
        history[row["instrument_id"]].append(_signal_point(row))
    instruments = []
    for instrument in ctx.master.search("", on):
        points = history[instrument.instrument_id][-OVERVIEW_HISTORY_POINTS:]
        instruments.append(
            s.InstrumentOverviewOut(
                instrument=_instrument_out(instrument, on),
                event_count=event_counts.get(instrument.instrument_id, 0),
                latest_signal=points[-1] if points else None,
                score_history=points,
            )
        )
    return s.OverviewResponse(
        feed=s.FeedOut(
            mode=ctx.settings.feed_mode,
            earliest_available_at=earliest,
            latest_available_at=latest,
        ),
        pipeline=s.PipelineCountsOut(**counts),
        instruments=instruments,
        meta=ctx.meta(as_of),
    )


@v1.get("/dead-letters", response_model=s.DeadLettersResponse, tags=["operations"])
async def list_dead_letters(ctx: Context, limit: PageLimit = 50) -> s.DeadLettersResponse:
    rows = await ctx.read_model.list_dead_letters(limit)
    return s.DeadLettersResponse(data=[s.DeadLetterOut(**row) for row in rows], meta=ctx.meta(None))
