"use client";

import Link from "next/link";

import { useAppState } from "@/components/AppState";
import { ErrorState, Loading, Sparkline, StanceTag } from "@/components/primitives";
import { formatSigned } from "@/lib/format";
import type { OverviewResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function IssuersPage() {
  const { asOf, refreshMs } = useAppState();
  const overview = useApi<OverviewResponse>("v1/overview", { as_of: asOf }, { refreshMs });

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Issuers</h1>
          <p className="page-sub">
            The latest decayed sentiment score for each covered issuer. A summary of news tone
            only: unvalidated, and not a view on price.
          </p>
        </div>
      </div>

      {overview.error && !overview.data ? (
        <ErrorState message={overview.error.message} />
      ) : !overview.data ? (
        <Loading label="Loading issuers" />
      ) : (
        <div className={`watch-grid${overview.isStale ? " is-stale" : ""}`}>
          {overview.data.instruments.map(({ instrument, event_count, latest_signal, score_history }) => (
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
                    <span className="stance">No signal yet</span>
                  )}
                </div>
                <Sparkline points={score_history} />
              </div>
              <div className="card-note">
                {event_count} {event_count === 1 ? "event" : "events"}
                {instrument.all_symbols.length > 1
                  ? ` · formerly ${instrument.all_symbols.filter((s) => s !== instrument.symbol).join(", ")}`
                  : ""}
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
