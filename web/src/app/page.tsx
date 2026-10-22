"use client";

import { useState } from "react";

import { useAppState } from "@/components/AppState";
import { EventDrawer } from "@/components/EventDrawer";
import { EventTable } from "@/components/EventTable";
import { ErrorState, Loading, StatTile } from "@/components/primitives";
import { formatNumber, formatPercent } from "@/lib/format";
import type { EventItem, EventsResponse, OverviewResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const PAGE_STEPS = [50, 100, 200] as const;

export default function TapePage() {
  const { asOf, refreshMs } = useAppState();
  const [symbol, setSymbol] = useState("");
  const [hideDuplicates, setHideDuplicates] = useState(false);
  const [step, setStep] = useState(0);
  const [selected, setSelected] = useState<EventItem | null>(null);
  const limit = PAGE_STEPS[step] ?? 200;

  const overview = useApi<OverviewResponse>("v1/overview", { as_of: asOf }, { refreshMs });
  const events = useApi<EventsResponse>(
    "v1/events",
    {
      as_of: asOf,
      symbol: symbol || null,
      include_duplicates: hideDuplicates ? "false" : null,
      limit,
    },
    { refreshMs },
  );

  const counts = overview.data?.pipeline;
  const rows = events.data?.data ?? [];
  const instruments = overview.data?.instruments ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Event tape</h1>
          <p className="page-sub">
            Every news item the pipeline accepted, newest first, with when it became usable,
            which issuer it was linked to, and how the baseline sentiment model scored it.
            Select a row for full provenance.
          </p>
        </div>
      </div>

      {counts ? (
        <div className={`tiles${overview.isStale ? " is-stale" : ""}`}>
          <StatTile
            label="Events"
            value={formatNumber(counts.events)}
            sub={`${formatNumber(counts.duplicates)} exact duplicates`}
          />
          <StatTile
            label="Linked to an issuer"
            value={formatNumber(counts.events - counts.unresolved)}
            sub={`${formatNumber(counts.unresolved)} unresolved`}
          />
          <StatTile
            label="Scored"
            value={formatNumber(counts.scored)}
            sub={`${formatPercent(counts.sentiment_abstained, counts.scored)} abstained`}
          />
          <StatTile
            label="Signals"
            value={formatNumber(counts.signals)}
            sub={`${formatNumber(counts.signals_abstained)} abstained`}
          />
          <StatTile
            label="Quarantined"
            value={formatNumber(counts.dead_letters)}
            sub="dead-letter topic"
          />
        </div>
      ) : null}

      <div className="filters" role="group" aria-label="Filters">
        <label className="field">
          Issuer
          <select value={symbol} onChange={(change) => setSymbol(change.target.value)}>
            <option value="">All issuers</option>
            {instruments.map(({ instrument }) => (
              <option key={instrument.instrument_id} value={instrument.symbol}>
                {instrument.symbol} · {instrument.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <input
            type="checkbox"
            checked={hideDuplicates}
            onChange={(change) => setHideDuplicates(change.target.checked)}
          />
          Hide duplicates
        </label>
      </div>

      {events.error && !events.data ? (
        <ErrorState message={events.error.message} />
      ) : events.isLoading ? (
        <Loading label="Loading events" />
      ) : rows.length === 0 ? (
        <div className="card empty">
          {asOf ? "Nothing had arrived yet at the pinned time." : "No events yet. Run "}
          {asOf ? null : (
            <>
              <code>make seed</code> or <code>make dev</code>.
            </>
          )}
        </div>
      ) : (
        <div className={events.isStale ? "is-stale" : undefined}>
          <EventTable events={rows} onSelect={setSelected} caption="Event tape, newest first" />
          <div className="table-foot">
            <span>
              Showing the newest {formatNumber(rows.length)}
              {symbol ? ` for ${symbol}` : ""}. Times are UTC.
            </span>
            {events.data?.page.next_cursor ? (
              step < PAGE_STEPS.length - 1 ? (
                <button type="button" className="btn" onClick={() => setStep(step + 1)}>
                  Show more
                </button>
              ) : (
                <span>Filter by issuer or pin an earlier time to see older items.</span>
              )
            ) : null}
          </div>
        </div>
      )}

      <EventDrawer event={selected} onClose={() => setSelected(null)} />
    </>
  );
}
