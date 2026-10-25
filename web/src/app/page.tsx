"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { useAppState } from "@/components/AppState";
import { DailyFlow, SentimentHeatmap, SignalLeaderboard, ToneMix, toneOf } from "@/components/charts";
import { EventDrawer } from "@/components/EventDrawer";
import { ErrorState, Loading, PolarityBar, StatTile } from "@/components/primitives";
import { formatDay, formatNumber, formatSigned } from "@/lib/format";
import { MARKET_LABEL, marketOf, type Market } from "@/lib/market";
import type { EventItem, EventsResponse, OverviewResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const HEADLINE_COUNT = 4;

/** Weight of a headline in the aggregate: how strongly it reads, times how much it matters. */
function weight(event: EventItem): number {
  return Math.abs(event.sentiment!.signed_sentiment) * event.sentiment!.materiality;
}

function HeadlineList({
  title,
  events,
  onSelect,
}: {
  title: string;
  events: EventItem[];
  onSelect: (event: EventItem) => void;
}) {
  return (
    <div className="headline-col">
      <h3 className="headline-col-title">{title}</h3>
      {events.length === 0 ? (
        <p className="muted">None in range.</p>
      ) : (
        <ul className="headlines">
          {events.map((event) => (
            <li key={event.event_id}>
              <button type="button" className="headline-row" onClick={() => onSelect(event)}>
                <PolarityBar value={event.sentiment!.signed_sentiment} />
                <span className="headline-text">{event.headline}</span>
                <span className="headline-meta">
                  {event.instruments[0] ? <span className="chip mono">{event.instruments[0].symbol}</span> : null}
                  <span className="muted num">{formatDay(event.available_at)}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function OverviewPage() {
  const { asOf, refreshMs } = useAppState();
  const [selected, setSelected] = useState<EventItem | null>(null);
  const overview = useApi<OverviewResponse>("v1/overview", { as_of: asOf }, { refreshMs });
  const feed = useApi<EventsResponse>("v1/events", { as_of: asOf, limit: 200 }, { refreshMs });

  const events = useMemo(() => feed.data?.data ?? [], [feed.data]);
  const instruments = useMemo(() => overview.data?.instruments ?? [], [overview.data]);

  const stats = useMemo(() => {
    const used = events.filter((event) => ["pos", "neg", "neutral"].includes(toneOf(event)));
    const mean = used.length
      ? used.reduce((sum, event) => sum + event.sentiment!.signed_sentiment, 0) / used.length
      : null;
    const latest = instruments.map((item) => item.latest_signal).filter((signal) => signal && !signal.abstained);
    return {
      mean,
      positive: used.filter((event) => toneOf(event) === "pos").length,
      negative: used.filter((event) => toneOf(event) === "neg").length,
      leansUp: latest.filter((signal) => signal!.stance === "positive").length,
      leansDown: latest.filter((signal) => signal!.stance === "negative").length,
    };
  }, [events, instruments]);

  const strongest = useMemo(() => {
    const top = (tone: "pos" | "neg") =>
      events
        .filter((event) => toneOf(event) === tone)
        .sort((a, b) => weight(b) - weight(a) || b.available_at.localeCompare(a.available_at))
        .slice(0, HEADLINE_COUNT);
    return { positive: top("pos"), negative: top("neg") };
  }, [events]);

  const coverage = useMemo(() => {
    const groups: Record<Market, typeof instruments> = { us: [], nse: [], replay: [] };
    for (const item of instruments) groups[marketOf(item.instrument.instrument_id)].push(item);
    return groups;
  }, [instruments]);

  if (overview.error && !overview.data) return <ErrorState message={overview.error.message} />;
  if (!overview.data) return <Loading label="Loading market overview" />;

  const counts = overview.data.pipeline;
  const stale = overview.isStale || feed.isStale;

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Market overview</h1>
          <p className="page-sub">
            News tone across every covered issuer: who is leaning which way, how the flow of
            items has moved day by day, and the headlines carrying the most weight right now.
          </p>
        </div>
        <Link href="/research" className="btn btn-accent">
          Open research notebook
        </Link>
      </div>

      <div className={`tiles${stale ? " is-stale" : ""}`}>
        <StatTile
          label="Issuers covered"
          value={formatNumber(instruments.length)}
          sub={(Object.keys(coverage) as Market[])
            .filter((market) => coverage[market].length > 0)
            .map((market) => `${coverage[market].length} ${MARKET_LABEL[market]}`)
            .join(" · ")}
        />
        <StatTile
          label="News items"
          value={formatNumber(counts.events)}
          sub={`${formatNumber(counts.duplicates)} duplicates collapsed`}
        />
        <StatTile
          label="Mean tone"
          value={stats.mean === null ? "—" : formatSigned(stats.mean)}
          sub={`${stats.positive} positive · ${stats.negative} negative`}
        />
        <StatTile
          label="Issuer leans"
          value={`${stats.leansUp} / ${stats.leansDown}`}
          sub="positive / negative"
        />
        <StatTile label="Signals emitted" value={formatNumber(counts.signals)} sub={`${counts.signals_abstained} abstained`} />
      </div>

      <div className={`overview-grid${stale ? " is-stale" : ""}`}>
        <SignalLeaderboard instruments={instruments} />
        <div className="stack">
          <ToneMix events={events} />
          <section className="card">
            <div className="card-head">
              <h2 className="section-title">Coverage</h2>
              <span className="card-note">{instruments.length} issuers</span>
            </div>
            <div className="card-body coverage">
              {(Object.keys(coverage) as Market[]).filter((market) => coverage[market].length > 0).map((market) => {
                const group = coverage[market];
                // One headline can name two issuers, so these are mentions, not distinct items.
                const mentions = group.reduce((sum, item) => sum + item.event_count, 0);
                return (
                  <div key={market} className="coverage-group">
                    <div className="coverage-head">
                      <span className="coverage-name">{MARKET_LABEL[market]}</span>
                      <span className="card-note num">
                        {group.length} issuers · {formatNumber(mentions)} mentions
                      </span>
                    </div>
                    <div className="coverage-chips">
                      {group.map(({ instrument, event_count }) => (
                        <Link
                          key={instrument.instrument_id}
                          href={`/issuers/${instrument.symbol}`}
                          className="chip chip-link"
                          title={`${instrument.name} · ${instrument.sector} · ${event_count} mentions`}
                        >
                          <span className="mono">{instrument.symbol}</span>
                        </Link>
                      ))}
                    </div>
                    {market === "nse" && mentions === 0 ? (
                      <p className="card-note">Tracked for symbol resolution; news flow starts when a licensed NSE source is connected.</p>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </section>
        </div>
      </div>

      <div className={stale ? "is-stale" : undefined} style={{ marginBottom: 16 }}>
        <DailyFlow events={events} />
      </div>

      <div className={stale ? "is-stale" : undefined} style={{ marginBottom: 16 }}>
        <SentimentHeatmap events={events} instruments={instruments} />
      </div>

      <section className={`card${stale ? " is-stale" : ""}`}>
        <div className="card-head">
          <h2 className="section-title">Strongest headlines</h2>
          <span className="card-note">ranked by |tone| × materiality · select one for provenance</span>
        </div>
        <div className="card-body headline-cols">
          <HeadlineList title="Most positive" events={strongest.positive} onSelect={setSelected} />
          <HeadlineList title="Most negative" events={strongest.negative} onSelect={setSelected} />
        </div>
      </section>

      <EventDrawer event={selected} onClose={() => setSelected(null)} />
    </>
  );
}
