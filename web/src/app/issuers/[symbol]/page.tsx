"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { useAppState } from "@/components/AppState";
import { DotTimeline } from "@/components/DotTimeline";
import { EventDrawer } from "@/components/EventDrawer";
import { EventTable } from "@/components/EventTable";
import { Dot, ErrorState, Loading, StatTile } from "@/components/primitives";
import { Contributions, SignalCard } from "@/components/SignalPanel";
import { formatSigned } from "@/lib/format";
import type { EventItem, EventsResponse, SentimentResponse, SignalsResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const WINDOWS = [
  { value: "24h", label: "24 hours" },
  { value: "3d", label: "3 days" },
  { value: "7d", label: "7 days" },
  { value: "14d", label: "14 days" },
] as const;
const DEFAULT_THRESHOLD = 0.15;

export default function IssuerPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = decodeURIComponent(params.symbol ?? "").toUpperCase();
  const { asOf, refreshMs } = useAppState();
  const [range, setRange] = useState<string>("7d");
  const [selected, setSelected] = useState<EventItem | null>(null);

  const sentiment = useApi<SentimentResponse>(`v1/sentiment/${encodeURIComponent(symbol)}`, { window: range, as_of: asOf }, { refreshMs });
  const signals = useApi<SignalsResponse>(`v1/signals/${encodeURIComponent(symbol)}`, { as_of: asOf, limit: 500 }, { refreshMs });
  const events = useApi<EventsResponse>("v1/events", { symbol, as_of: asOf, limit: 200 }, { refreshMs });

  const failure = sentiment.error ?? signals.error;
  if (failure && !sentiment.data) {
    return (
      <>
        <p style={{ marginBottom: 16 }}>
          <Link href="/issuers" className="btn">All issuers</Link>
        </p>
        <ErrorState message={failure.message} />
      </>
    );
  }
  if (!sentiment.data || !signals.data) return <Loading label={`Loading ${symbol}`} />;

  const { instrument, summary, points } = sentiment.data;
  const latest = signals.data.latest;
  const end = Date.parse(sentiment.data.meta.as_of ?? new Date().toISOString());
  const start = Date.parse(sentiment.data.window_start);
  const rows = events.data?.data ?? [];
  const select = (eventId: string) => setSelected(rows.find((event) => event.event_id === eventId) ?? null);
  const formerly = instrument.all_symbols.filter((item) => item !== instrument.symbol);
  const stale = sentiment.isStale || signals.isStale;

  return (
    <>
      <div className="page-head">
        <div>
          <p className="card-note" style={{ marginBottom: 6 }}>
            <Link href="/issuers">Issuers</Link> / {instrument.sector}
          </p>
          <h1 className="page-title">
            <span className="mono">{instrument.symbol}</span> · {instrument.name}
          </h1>
          <p className="page-sub">
            {formerly.length ? `Traded as ${formerly.join(", ")} before its ticker change. ` : ""}
            News tone over the selected window, and the heuristic score built from it.
          </p>
        </div>
        <div className="segmented" role="group" aria-label="Time window">
          {WINDOWS.map((option) => (
            <button
              key={option.value}
              type="button"
              aria-pressed={range === option.value}
              onClick={() => setRange(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <div className={`tiles${stale ? " is-stale" : ""}`}>
        <StatTile label="Events in window" value={summary.events} sub={`${summary.duplicates} duplicates`} />
        <StatTile label="Used by the score" value={summary.used} sub={`${summary.abstained} abstained`} />
        <StatTile
          label="Tone of used events"
          value={`${summary.positive} / ${summary.negative}`}
          sub="positive / negative"
        />
        <StatTile
          label="Mean signed sentiment"
          value={summary.mean_signed_sentiment === null ? "—" : formatSigned(summary.mean_signed_sentiment)}
          sub={summary.mean_entropy === null ? "no usable events" : `mean entropy ${summary.mean_entropy.toFixed(2)}`}
        />
      </div>

      <div className="issuer-grid">
        <div className="stack">
          <section className={`card${stale ? " is-stale" : ""}`}>
            <div className="card-head">
              <h2 className="section-title">Sentiment timeline</h2>
              <div className="legend">
                <span className="legend-item"><Dot sign="pos" /> Positive event</span>
                <span className="legend-item"><Dot sign="neg" /> Negative event</span>
                <span className="legend-item"><Dot sign="zero" hollow /> Not used</span>
                <span className="legend-item"><span className="legend-line" /> Decayed score</span>
                <span className="legend-item"><span className="legend-band" /> Neutral band</span>
              </div>
            </div>
            <div className="card-body">
              <DotTimeline
                points={points}
                signals={signals.data.history}
                start={start}
                end={end}
                threshold={latest?.params.stance_threshold ?? DEFAULT_THRESHOLD}
                onSelect={select}
              />
              <p className="card-note" style={{ marginTop: 8 }}>
                Dot size is the event&apos;s weight (issuer-link confidence × materiality prior).
                Select a dot for its provenance.
              </p>
            </div>
          </section>
          <Contributions signal={latest} events={rows} onSelect={select} />
        </div>
        <SignalCard signal={latest} />
      </div>

      <h2 className="section-title" style={{ margin: "8px 0 12px" }}>
        Events for {instrument.symbol}
      </h2>
      {rows.length ? (
        <div className={events.isStale ? "is-stale" : undefined}>
          <EventTable events={rows} onSelect={setSelected} caption={`Events for ${instrument.symbol}`} />
        </div>
      ) : (
        <div className="card empty">No events for this issuer as of the selected time.</div>
      )}

      <EventDrawer event={selected} onClose={() => setSelected(null)} />
    </>
  );
}
