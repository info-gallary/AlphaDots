"use client";

import Link from "next/link";
import { useState } from "react";

import { useAppState } from "@/components/AppState";
import { ErrorState, Loading, Sparkline, StanceTag } from "@/components/primitives";
import { formatSigned } from "@/lib/format";
import { MARKET_LABEL, marketOf, type Market } from "@/lib/market";
import type { OverviewResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const FILTERS: { value: Market | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "replay", label: MARKET_LABEL.replay },
  { value: "nse", label: MARKET_LABEL.nse },
];

export default function IssuersPage() {
  const { asOf, refreshMs } = useAppState();
  const [market, setMarket] = useState<Market | "all">("all");
  const overview = useApi<OverviewResponse>("v1/overview", { as_of: asOf }, { refreshMs });
  // Issuers with news first (busiest first), then the ones still awaiting a source.
  const instruments = (overview.data?.instruments ?? [])
    .filter(({ instrument }) => market === "all" || marketOf(instrument.instrument_id) === market)
    .sort((a, b) => b.event_count - a.event_count || a.instrument.symbol.localeCompare(b.instrument.symbol));

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Issuers</h1>
          <p className="page-sub">
            The latest decayed news-tone score for each covered issuer, with its recent path. A
            summary of news tone, not a view on price.
          </p>
        </div>
        <div className="segmented" role="group" aria-label="Market">
          {FILTERS.map((option) => (
            <button
              key={option.value}
              type="button"
              aria-pressed={market === option.value}
              onClick={() => setMarket(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {overview.error && !overview.data ? (
        <ErrorState message={overview.error.message} />
      ) : !overview.data ? (
        <Loading label="Loading issuers" />
      ) : (
        <div className={`watch-grid${overview.isStale ? " is-stale" : ""}`}>
          {instruments.map(({ instrument, event_count, latest_signal, score_history }) => (
            <Link key={instrument.instrument_id} href={`/issuers/${instrument.symbol}`} className="watch-card">
              <div className="watch-top">
                <div>
                  <div className="watch-symbol mono">{instrument.symbol}</div>
                  <div className="watch-name">{instrument.name}</div>
                </div>
                <span className="chip">{instrument.sector}</span>
              </div>
              <div className="watch-bottom">
                <div>
                  <div className="watch-score num">
                    {latest_signal ? formatSigned(latest_signal.score_normalized) : "—"}
                  </div>
                  {latest_signal ? (
                    <StanceTag signal={latest_signal} />
                  ) : (
                    <span className="stance">Awaiting news</span>
                  )}
                </div>
                <Sparkline points={score_history} />
              </div>
              <div className="card-note watch-foot">
                <span>
                  {event_count} {event_count === 1 ? "item" : "items"}
                  {instrument.all_symbols.length > 1
                    ? ` · formerly ${instrument.all_symbols.filter((s) => s !== instrument.symbol).join(", ")}`
                    : ""}
                </span>
                <span>{MARKET_LABEL[marketOf(instrument.instrument_id)]}</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
