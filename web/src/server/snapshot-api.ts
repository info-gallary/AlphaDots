/**
 * The AlphaDots read API, answered from a static snapshot instead of a database.
 *
 * Used when the dashboard runs without a reachable API (for example on Vercel, which
 * cannot host the streaming pipeline). It mirrors `services/api/src/alphadots_api/
 * read_model.py` and `routes.py` rule for rule; most importantly the point-in-time rule:
 * with `as_of` set, a row is visible only if its own `available_at <= as_of`, checked
 * separately for an event and for its sentiment score.
 *
 * `web/test/parity.test.ts` replays requests recorded from the real FastAPI app
 * (`scripts/export_snapshot.py`) and requires identical answers. If you change a rule
 * here or in Python, that test is what tells you the two have diverged.
 *
 * This module is dependency-free and only imports types, so Node can run it directly.
 */

import type {
  ClusterMember,
  DeadLetter,
  EventItem,
  Instrument,
  InstrumentOverview,
  Meta,
  PipelineCounts,
  SentimentPoint,
  Signal,
  SignalPoint,
} from "../lib/types";
import type { Snapshot, SnapshotInstrument } from "./snapshot-types";

export interface ApiResult {
  status: number;
  body: unknown;
}

const DISCLAIMER = "Research output. Not investment advice and not an instruction to trade.";
const MAX_PAGE_SIZE = 200;
const MAX_SENTIMENT_POINTS = 500;
const MAX_SIGNAL_HISTORY = 500;
const OVERVIEW_SIGNAL_SCAN = 5000;
const OVERVIEW_HISTORY_POINTS = 60;
const WINDOW_RE = /^([1-9]\d{0,3})([mhd])$/;
const UNIT_SECONDS: Record<string, number> = { m: 60, h: 3600, d: 86_400 };
const ISO_RE =
  /^(\d{4})-(\d{2})-(\d{2})(?:[Tt ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,6}))?)?)?(?:([Zz])|([+-])(\d{2}):?(\d{2}))?$/;
const BASE64_ALPHABET = /[^A-Za-z0-9\-_=]/g;

class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const invalid = () => new ApiError(422, "invalid_parameter", "request validation failed");

// ----------------------------------------------------------------- time helpers

interface Moment {
  ms: number;
  hasOffset: boolean;
}

function parseIso(text: string): Moment | null {
  const match = ISO_RE.exec(text);
  if (!match) return null;
  const [, y, mo, d, h = "0", mi = "0", s = "0", frac = "", zulu, sign, oh, om] = match;
  const fraction = Number(`0.${frac || "0"}`);
  let ms = Date.UTC(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s));
  const check = new Date(ms);
  if (
    check.getUTCFullYear() !== Number(y) ||
    check.getUTCMonth() !== Number(mo) - 1 ||
    check.getUTCDate() !== Number(d) ||
    Number(h) > 23 ||
    Number(mi) > 59 ||
    Number(s) > 59
  ) {
    return null;
  }
  ms += Math.floor(fraction * 1000);
  const hasOffset = Boolean(zulu || sign);
  if (sign) ms -= (sign === "+" ? 1 : -1) * (Number(oh) * 60 + Number(om)) * 60_000;
  return { ms, hasOffset };
}

const toMs = (iso: string): number => {
  const parsed = parseIso(iso);
  if (!parsed) throw new Error(`snapshot contains an invalid timestamp: ${iso}`);
  return parsed.ms;
};

function fraction(ms: number): string {
  const remainder = ((ms % 1000) + 1000) % 1000;
  return remainder === 0 ? "" : `.${String(remainder).padStart(3, "0")}000`;
}

/** Pydantic's JSON form: `2026-03-02T13:30:06Z`, microseconds only when non-zero. */
function isoZ(ms: number): string {
  return `${new Date(ms).toISOString().slice(0, 19)}${fraction(ms)}Z`;
}

/** Python's `datetime.isoformat()` for an aware UTC value, as used in cursors. */
function isoPython(ms: number): string {
  return `${new Date(ms).toISOString().slice(0, 19)}${fraction(ms)}+00:00`;
}

function utcDate(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

// --------------------------------------------------------------- parameters

function single(query: URLSearchParams, name: string): string | null {
  const values = query.getAll(name);
  return values.length ? (values[values.length - 1] ?? null) : null;
}

function stringParam(query: URLSearchParams, name: string, maxLength: number): string | null {
  const value = single(query, name);
  if (value !== null && value.length > maxLength) throw invalid();
  return value;
}

function intParam(
  query: URLSearchParams,
  name: string,
  fallback: number,
  min: number,
  max: number,
): number {
  const raw = single(query, name);
  if (raw === null) return fallback;
  if (!/^\s*[+-]?\d+\s*$/.test(raw)) throw invalid();
  const value = Number(raw);
  if (value < min || value > max) throw invalid();
  return value;
}

function boolParam(query: URLSearchParams, name: string, fallback: boolean): boolean {
  const raw = single(query, name);
  if (raw === null) return fallback;
  const text = raw.trim().toLowerCase();
  if (["true", "1", "yes", "on", "t", "y"].includes(text)) return true;
  if (["false", "0", "no", "off", "f", "n"].includes(text)) return false;
  throw invalid();
}

function asOfParam(query: URLSearchParams): number | null {
  const raw = single(query, "as_of");
  if (raw === null) return null;
  const parsed = parseIso(raw.trim());
  if (!parsed) throw invalid();
  if (!parsed.hasOffset) {
    throw new ApiError(
      422,
      "invalid_parameter",
      "as_of must include a UTC offset, for example 2026-03-05T14:00:00Z",
    );
  }
  return parsed.ms;
}

/** Python `repr()` of a plain string, for messages that quote user input. */
function pyRepr(text: string): string {
  if (text.includes("'") && !text.includes('"')) return `"${text}"`;
  return `'${text.replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;
}

function encodeCursor(availableMs: number, eventId: string): string {
  const bytes = new TextEncoder().encode(`${isoPython(availableMs)}|${eventId}`);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_");
}

function decodeCursor(cursor: string): [number, string] {
  const failure = new ApiError(400, "invalid_cursor", "cursor is not valid; restart pagination");
  // Like Python's lenient decoder: characters outside the alphabet are discarded.
  const cleaned = cursor.replace(BASE64_ALPHABET, "");
  if (cleaned.length % 4 !== 0) throw failure;
  let decoded: string;
  try {
    const binary = atob(cleaned.replace(/-/g, "+").replace(/_/g, "/"));
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw failure;
  }
  const separator = decoded.indexOf("|");
  const stamp = separator === -1 ? decoded : decoded.slice(0, separator);
  const eventId = separator === -1 ? "" : decoded.slice(separator + 1);
  const parsed = parseIso(stamp);
  if (!parsed || !parsed.hasOffset || !eventId) throw failure;
  return [parsed.ms, eventId];
}

function parseWindow(text: string): number {
  const match = WINDOW_RE.exec(text.trim().toLowerCase());
  if (!match) {
    throw new ApiError(
      422,
      "invalid_parameter",
      `invalid window ${pyRepr(text)}; expected forms like 30m, 24h, 7d`,
    );
  }
  return Number(match[1]) * (UNIT_SECONDS[match[2]!] ?? 0) * 1000;
}

// ------------------------------------------------------------- indexed data

interface IndexedEvent {
  event: EventItem;
  availableMs: number;
  sentimentMs: number | null;
  instrumentIds: Set<string>;
}

interface IndexedSignal {
  signal: Signal;
  asOfMs: number;
  availableMs: number;
}

class Master {
  private readonly instruments: SnapshotInstrument[];

  constructor(instruments: SnapshotInstrument[]) {
    this.instruments = instruments;
  }

  private static active(instrument: SnapshotInstrument, on: string): string | null {
    const listing = instrument.listings.find(
      (item) => item.valid_from <= on && (item.valid_to === null || on <= item.valid_to),
    );
    return listing ? listing.symbol : null;
  }

  all(): SnapshotInstrument[] {
    return [...this.instruments].sort((a, b) => compare(a.instrument_id, b.instrument_id));
  }

  symbolOn(instrument: SnapshotInstrument, on: string): string | null {
    return Master.active(instrument, on);
  }

  allSymbols(instrument: SnapshotInstrument): string[] {
    return [...new Set(instrument.listings.map((item) => item.symbol))];
  }

  bySymbol(symbol: string, on: string): SnapshotInstrument | undefined {
    const wanted = symbol.trim().toUpperCase();
    return this.instruments.find((instrument) =>
      instrument.listings.some(
        (item) =>
          item.symbol === wanted &&
          item.valid_from <= on &&
          (item.valid_to === null || on <= item.valid_to),
      ),
    );
  }

  everListedAs(symbol: string): SnapshotInstrument[] {
    const wanted = symbol.trim().toUpperCase();
    return this.all().filter((instrument) => this.allSymbols(instrument).includes(wanted));
  }

  search(query: string, on: string): SnapshotInstrument[] {
    const needle = query.trim().toLowerCase();
    return this.all().filter((instrument) => {
      const symbol = Master.active(instrument, on);
      if (symbol === null) return false;
      if (!needle) return true;
      return [instrument.name, symbol, ...instrument.aliases].some((text) =>
        text.toLowerCase().includes(needle),
      );
    });
  }

  out(instrument: SnapshotInstrument, on: string): Instrument {
    const symbols = this.allSymbols(instrument);
    return {
      instrument_id: instrument.instrument_id,
      symbol: Master.active(instrument, on) ?? symbols[symbols.length - 1] ?? "",
      name: instrument.name,
      sector: instrument.sector,
      all_symbols: symbols,
    };
  }
}

/** Code-point order, the same order Python and the database use for these IDs. */
function compare(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

class SnapshotIndex {
  readonly events: IndexedEvent[];
  readonly signals: IndexedSignal[];
  readonly master: Master;
  readonly earliestMs: number | null;
  readonly latestMs: number | null;
  readonly snapshot: Snapshot;

  constructor(snapshot: Snapshot) {
    this.snapshot = snapshot;
    this.events = snapshot.events.map((event) => ({
      event,
      availableMs: toMs(event.available_at),
      sentimentMs: event.sentiment ? toMs(event.sentiment.available_at) : null,
      instrumentIds: new Set(event.instruments.map((ref) => ref.instrument_id)),
    }));
    this.signals = snapshot.signals.map((signal) => ({
      signal,
      asOfMs: toMs(signal.as_of),
      availableMs: toMs(signal.available_at),
    }));
    this.master = new Master(snapshot.reference.instruments);
    const eventTimes = this.events.map((item) => item.availableMs);
    const everything = [
      ...eventTimes,
      ...this.events.flatMap((item) => (item.sentimentMs === null ? [] : [item.sentimentMs])),
      ...this.signals.map((item) => item.availableMs),
    ];
    this.earliestMs = eventTimes.length ? Math.min(...eventTimes) : null;
    this.latestMs = everything.length ? Math.max(...everything) : null;
  }
}

const indexCache = new WeakMap<Snapshot, SnapshotIndex>();

function indexFor(snapshot: Snapshot): SnapshotIndex {
  let index = indexCache.get(snapshot);
  if (!index) {
    index = new SnapshotIndex(snapshot);
    indexCache.set(snapshot, index);
  }
  return index;
}

// ------------------------------------------------------------------- queries

const visible = (availableMs: number, asOf: number | null) => asOf === null || availableMs <= asOf;

function eventOut(item: IndexedEvent, asOf: number | null): EventItem {
  const sentimentVisible = item.sentimentMs !== null && visible(item.sentimentMs, asOf);
  return { ...item.event, sentiment: sentimentVisible ? item.event.sentiment : null };
}

function newestFirst(a: IndexedEvent, b: IndexedEvent): number {
  return b.availableMs - a.availableMs || compare(b.event.event_id, a.event.event_id);
}

function signalPoint(item: IndexedSignal): SignalPoint {
  const { signal } = item;
  return {
    signal_id: signal.event_id,
    as_of: signal.as_of,
    available_at: signal.available_at,
    score: signal.score,
    score_normalized: signal.score_normalized,
    stance: signal.stance,
    abstained: signal.abstained,
  };
}

/** Most recent `limit` signals matching `keep`, returned oldest first. */
function recentSignals(
  index: SnapshotIndex,
  asOf: number | null,
  limit: number,
  keep: (item: IndexedSignal) => boolean = () => true,
): IndexedSignal[] {
  return index.signals
    .filter((item) => keep(item) && visible(item.availableMs, asOf))
    .sort((a, b) => b.asOfMs - a.asOfMs || compare(b.signal.event_id, a.signal.event_id))
    .slice(0, limit)
    .reverse();
}

// -------------------------------------------------------------------- routes

interface Context {
  index: SnapshotIndex;
  query: URLSearchParams;
  now: number;
}

function meta(ctx: Context, asOf: number | null): Meta {
  return {
    schema_version: "1.0",
    generated_at: isoZ(ctx.now),
    as_of: asOf === null ? null : isoZ(asOf),
    feed_mode: ctx.index.snapshot.feed_mode,
    research_only: true,
    disclaimer: DISCLAIMER,
  };
}

function referenceDate(ctx: Context, asOf: number | null): string {
  return utcDate(asOf ?? ctx.index.latestMs ?? ctx.now);
}

function resolveSymbol(
  ctx: Context,
  symbol: string,
  asOf: number | null,
): [SnapshotInstrument, string] {
  const on = referenceDate(ctx, asOf);
  let instrument = ctx.index.master.bySymbol(symbol, on);
  if (!instrument) {
    const historical = ctx.index.master.everListedAs(symbol);
    if (historical.length === 1) instrument = historical[0];
  }
  if (!instrument) {
    throw new ApiError(404, "instrument_not_found", `no instrument is known as ${pyRepr(symbol)}`);
  }
  return [instrument, on];
}

function listInstruments(ctx: Context) {
  const asOf = asOfParam(ctx.query);
  const query = stringParam(ctx.query, "query", 64) ?? "";
  const on = referenceDate(ctx, asOf);
  const { master, snapshot } = ctx.index;
  return {
    data: master.search(query, on).map((item) => master.out(item, on)),
    reference_version: snapshot.reference.version,
    reference_license: snapshot.reference.license,
    meta: meta(ctx, asOf),
  };
}

function listEvents(ctx: Context) {
  const asOf = asOfParam(ctx.query);
  const symbol = stringParam(ctx.query, "symbol", 16);
  const limit = intParam(ctx.query, "limit", 50, 1, MAX_PAGE_SIZE);
  const rawCursor = stringParam(ctx.query, "cursor", 512);
  const includeDuplicates = boolParam(ctx.query, "include_duplicates", true);
  const instrumentId = symbol ? resolveSymbol(ctx, symbol, asOf)[0].instrument_id : null;
  const cursor = rawCursor ? decodeCursor(rawCursor) : null;

  const rows = ctx.index.events
    .filter((item) => {
      if (!visible(item.availableMs, asOf)) return false;
      if (instrumentId !== null && !item.instrumentIds.has(instrumentId)) return false;
      if (!includeDuplicates && item.event.duplicate_of !== null) return false;
      if (cursor !== null) {
        const [cursorMs, cursorId] = cursor;
        return (
          item.availableMs < cursorMs ||
          (item.availableMs === cursorMs && compare(item.event.event_id, cursorId) < 0)
        );
      }
      return true;
    })
    .sort(newestFirst)
    .slice(0, limit + 1);

  const page = rows.slice(0, limit);
  const last = page[page.length - 1];
  return {
    data: page.map((item) => eventOut(item, asOf)),
    page: {
      limit,
      next_cursor:
        rows.length > limit && last ? encodeCursor(last.availableMs, last.event.event_id) : null,
    },
    meta: meta(ctx, asOf),
  };
}

function getEvent(ctx: Context, eventId: string) {
  const asOf = asOfParam(ctx.query);
  const item = ctx.index.events.find(
    (candidate) => candidate.event.event_id === eventId && visible(candidate.availableMs, asOf),
  );
  if (!item) {
    throw new ApiError(404, "event_not_found", "no such event is visible as of this time");
  }
  const cluster: ClusterMember[] = ctx.index.events
    .filter(
      (candidate) =>
        candidate.event.cluster_id === item.event.cluster_id &&
        visible(candidate.availableMs, asOf),
    )
    .sort((a, b) => a.availableMs - b.availableMs || compare(a.event.event_id, b.event.event_id))
    .map(({ event }) => ({
      event_id: event.event_id,
      source: event.source,
      source_event_id: event.source_event_id,
      first_seen_at: event.first_seen_at,
      available_at: event.available_at,
      is_canonical: event.duplicate_of === null,
    }));
  return { data: eventOut(item, asOf), cluster, meta: meta(ctx, asOf) };
}

function getSentiment(ctx: Context, symbol: string) {
  const asOf = asOfParam(ctx.query);
  const window = stringParam(ctx.query, "window", 8) ?? "24h";
  const span = parseWindow(window);
  const [instrument, on] = resolveSymbol(ctx, symbol, asOf);
  // With no as_of the window ends at the newest data held, not at wall-clock now.
  const end = asOf ?? ctx.index.latestMs ?? ctx.now;
  const start = end - span;

  const rows = ctx.index.events
    .filter(
      (item) =>
        item.instrumentIds.has(instrument.instrument_id) &&
        item.availableMs <= end &&
        item.sentimentMs !== null &&
        item.sentimentMs > start &&
        item.sentimentMs <= end,
    )
    .sort(
      (a, b) =>
        (b.sentimentMs ?? 0) - (a.sentimentMs ?? 0) || compare(b.event.event_id, a.event.event_id),
    )
    .slice(0, MAX_SENTIMENT_POINTS + 1)
    .reverse();

  const points: SentimentPoint[] = rows.slice(-MAX_SENTIMENT_POINTS).map(({ event }) => {
    const sentiment = event.sentiment!;
    const relevance =
      event.instruments.find((ref) => ref.instrument_id === instrument.instrument_id)?.confidence ??
      0;
    return {
      event_id: event.event_id,
      headline: event.headline,
      source: event.source,
      published_at: event.published_at,
      available_at: sentiment.available_at,
      signed_sentiment: sentiment.signed_sentiment,
      entropy: sentiment.entropy,
      abstained: sentiment.abstained,
      abstain_reason: sentiment.abstain_reason,
      is_duplicate: event.duplicate_of !== null,
      event_type: sentiment.event_type,
      materiality: sentiment.materiality,
      relevance,
    };
  });
  const used = points.filter((point) => !point.abstained && !point.is_duplicate);
  const sum = (values: number[]) => values.reduce((total, value) => total + value, 0);

  return {
    instrument: ctx.index.master.out(instrument, on),
    window,
    window_start: isoZ(start),
    summary: {
      events: points.length,
      used: used.length,
      abstained: points.filter((point) => point.abstained && !point.is_duplicate).length,
      duplicates: points.filter((point) => point.is_duplicate).length,
      positive: used.filter((point) => point.signed_sentiment > 0).length,
      negative: used.filter((point) => point.signed_sentiment < 0).length,
      neutral: used.filter((point) => point.signed_sentiment === 0).length,
      mean_signed_sentiment: used.length
        ? sum(used.map((point) => point.signed_sentiment)) / used.length
        : null,
      mean_entropy: used.length ? sum(used.map((point) => point.entropy)) / used.length : null,
    },
    points,
    points_truncated: rows.length > MAX_SENTIMENT_POINTS,
    meta: meta(ctx, end),
  };
}

function getSignals(ctx: Context, symbol: string) {
  const asOf = asOfParam(ctx.query);
  const horizon = single(ctx.query, "horizon") ?? "1d";
  if (horizon !== "1d" && horizon !== "5d") throw invalid();
  const limit = intParam(ctx.query, "limit", 200, 1, MAX_SIGNAL_HISTORY);
  const [instrument, on] = resolveSymbol(ctx, symbol, asOf);
  const rows = recentSignals(
    ctx.index,
    asOf,
    limit,
    (item) =>
      item.signal.instrument.instrument_id === instrument.instrument_id &&
      item.signal.horizon === horizon,
  );
  return {
    instrument: ctx.index.master.out(instrument, on),
    horizon,
    latest: rows.length ? rows[rows.length - 1]!.signal : null,
    history: rows.map(signalPoint),
    meta: meta(ctx, asOf),
  };
}

function getOverview(ctx: Context) {
  const asOf = asOfParam(ctx.query);
  const { index } = ctx;
  const on = referenceDate(ctx, asOf);
  const shown = index.events.filter((item) => visible(item.availableMs, asOf));
  const scored = index.events.filter(
    (item) => item.sentimentMs !== null && visible(item.sentimentMs, asOf),
  );
  const signals = index.signals.filter((item) => visible(item.availableMs, asOf));
  const pipeline: PipelineCounts = {
    events: shown.length,
    duplicates: shown.filter((item) => item.event.duplicate_of !== null).length,
    unresolved: shown.filter((item) => item.event.instruments.length === 0).length,
    scored: scored.length,
    sentiment_abstained: scored.filter((item) => item.event.sentiment?.abstained).length,
    signals: signals.length,
    signals_abstained: signals.filter((item) => item.signal.abstained).length,
    // Operational records, stamped with processing time: never time-filtered.
    dead_letters: index.snapshot.dead_letters.length,
  };

  const eventCounts = new Map<string, number>();
  for (const item of shown) {
    for (const id of item.instrumentIds) eventCounts.set(id, (eventCounts.get(id) ?? 0) + 1);
  }
  const history = new Map<string, SignalPoint[]>();
  for (const item of recentSignals(index, asOf, OVERVIEW_SIGNAL_SCAN)) {
    const id = item.signal.instrument.instrument_id;
    history.set(id, [...(history.get(id) ?? []), signalPoint(item)]);
  }
  const instruments: InstrumentOverview[] = index.master.search("", on).map((instrument) => {
    const points = (history.get(instrument.instrument_id) ?? []).slice(-OVERVIEW_HISTORY_POINTS);
    return {
      instrument: index.master.out(instrument, on),
      event_count: eventCounts.get(instrument.instrument_id) ?? 0,
      latest_signal: points.length ? points[points.length - 1]! : null,
      score_history: points,
    };
  });

  return {
    feed: {
      mode: index.snapshot.feed_mode,
      earliest_available_at: index.earliestMs === null ? null : isoZ(index.earliestMs),
      latest_available_at: index.latestMs === null ? null : isoZ(index.latestMs),
    },
    pipeline,
    instruments,
    meta: meta(ctx, asOf),
  };
}

function listDeadLetters(ctx: Context) {
  const limit = intParam(ctx.query, "limit", 50, 1, MAX_PAGE_SIZE);
  const data: DeadLetter[] = [...ctx.index.snapshot.dead_letters]
    .sort((a, b) => toMs(b.failed_at) - toMs(a.failed_at) || compare(a.dlq_id, b.dlq_id))
    .slice(0, limit);
  return { data, meta: meta(ctx, null) };
}

/** Latest recorded brief for the issuer, visible only once its `available_at <= as_of`. */
function getResearch(ctx: Context, symbol: string) {
  const asOf = asOfParam(ctx.query);
  const [instrument] = resolveSymbol(ctx, symbol, asOf);
  const report =
    Object.values(ctx.index.snapshot.research ?? {})
      .filter(
        (item) =>
          item.instrument.instrument_id === instrument.instrument_id &&
          visible(toMs(item.available_at), asOf),
      )
      .sort((a, b) => toMs(b.as_of) - toMs(a.as_of) || compare(b.event_id, a.event_id))[0] ?? null;
  return { data: report, meta: meta(ctx, asOf) };
}

function getMonitor(ctx: Context) {
  const monitor = ctx.index.snapshot.monitor;
  if (!monitor) {
    throw new ApiError(503, "agent_unavailable", "This snapshot was exported without a monitor run.");
  }
  // Recorded once at export: always a cached answer, never time-filtered (operational).
  return { data: monitor, cached: true, meta: meta(ctx, null) };
}

function route(ctx: Context, segments: string[]): unknown {
  const [root, resource, id, ...rest] = segments;
  if (root === "health" && rest.length === 0 && id === undefined) {
    if (resource === "live") return { status: "ok" };
    if (resource === "ready") return { status: "ready", checks: { snapshot: "ok" } };
  }
  if (root !== "v1" || rest.length > 0) throw new ApiError(404, "not_found", "Not Found");
  if (id === undefined) {
    switch (resource) {
      case "instruments":
        return listInstruments(ctx);
      case "events":
        return listEvents(ctx);
      case "overview":
        return getOverview(ctx);
      case "dead-letters":
        return listDeadLetters(ctx);
      case "monitor":
        return getMonitor(ctx);
    }
  } else {
    switch (resource) {
      case "events":
        return getEvent(ctx, id);
      case "sentiment":
        return getSentiment(ctx, id);
      case "signals":
        return getSignals(ctx, id);
      case "research":
        return getResearch(ctx, id);
    }
  }
  throw new ApiError(404, "not_found", "Not Found");
}

/**
 * Answer one GET request. `segments` is the decoded path, for example
 * `["v1", "signals", "NWSC"]`; `now` is only used for `generated_at`.
 */
export function handleSnapshotRequest(
  snapshot: Snapshot,
  segments: string[],
  query: URLSearchParams,
  now: Date = new Date(),
  traceId = "snapshot",
): ApiResult {
  const ctx: Context = { index: indexFor(snapshot), query, now: now.getTime() };
  try {
    return { status: 200, body: route(ctx, segments) };
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    return {
      status: error.status,
      body: { error: { code: error.code, message: error.message, trace_id: traceId, details: null } },
    };
  }
}
