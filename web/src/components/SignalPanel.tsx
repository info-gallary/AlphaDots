"use client";

import { formatSigned, formatSpan, formatTime, humanize, shortId } from "@/lib/format";
import type { EventItem, Signal } from "@/lib/types";

import { WarningGlyph } from "./icons";
import { StanceTag, StatusTag } from "./primitives";

/** The latest heuristic signal, framed so it cannot be mistaken for a recommendation. */
export function SignalCard({ signal }: { signal: Signal | null }) {
  if (!signal) {
    return (
      <div className="card">
        <div className="card-head">
          <h2 className="section-title">Decayed sentiment score</h2>
        </div>
        <div className="card-body muted">No signal had been computed by the selected time.</div>
      </div>
    );
  }
  const { coverage, params } = signal;
  return (
    <div className="card">
      <div className="card-head">
        <h2 className="section-title">Decayed sentiment score</h2>
        <span className="card-note mono">{signal.horizon} · {signal.model_id}@{signal.model_version}</span>
      </div>
      <div className="card-body">
        <div className="signal-score">
          <strong className="num">{formatSigned(signal.score_normalized)}</strong>
          <StanceTag signal={signal} />
        </div>
        <p className="card-note">
          as of <span className="mono num">{formatTime(signal.as_of)} UTC</span>
          {signal.abstained ? ` · abstained: ${humanize(signal.abstain_reason ?? "")}` : ""}
        </p>

        <div className="callout" style={{ margin: "14px 0" }}>
          <WarningGlyph />
          <span>
            <strong>Unvalidated heuristic, not a recommendation.</strong> This summarises the
            tone of recent news. It has not been tested against returns, and its decay rate is
            a configured prior, not an estimate.
          </span>
        </div>

        <dl className="kv">
          <dt>Evidence used</dt>
          <dd className="num">
            {coverage.events_used} of {coverage.events_considered} events
          </dd>
          <dt>Excluded</dt>
          <dd className="num">
            {coverage.events_abstained} abstained · {coverage.duplicates_excluded} duplicates
          </dd>
          <dt>Effective weight</dt>
          <dd className="num">
            {coverage.effective_weight.toFixed(2)}{" "}
            <span className="muted">(abstains below {params.min_effective_weight})</span>
          </dd>
          <dt>Raw S(i,t)</dt>
          <dd className="num">{formatSigned(signal.score, 3)}</dd>
          <dt>Decay τ</dt>
          <dd>
            {formatSpan(params.tau_seconds)}{" "}
            <span className="muted">
              ({params.tau_source === "config_prior_unestimated" ? "configured, not estimated" : humanize(params.tau_source)})
            </span>
          </dd>
          <dt>Lookback</dt>
          <dd>{formatSpan(params.lookback_seconds)}</dd>
          <dt>Neutral band</dt>
          <dd className="num">±{params.stance_threshold.toFixed(2)}</dd>
          <dt>Validation</dt>
          <dd>{humanize(signal.validation_status)}</dd>
        </dl>

        {signal.risk_flags.length ? (
          <div className="flags" style={{ marginTop: 14 }}>
            {signal.risk_flags.map((flag) => (
              <StatusTag key={flag} tone="warning">
                {humanize(flag)}
              </StatusTag>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** Each contributing event as a signed bar from a shared zero line. */
export function Contributions({
  signal,
  events,
  onSelect,
}: {
  signal: Signal | null;
  events: EventItem[];
  onSelect: (eventId: string) => void;
}) {
  if (!signal || signal.contributions.length === 0) return null;
  const headlines = new Map(events.map((event) => [event.event_id, event.headline]));
  const largest = Math.max(...signal.contributions.map((item) => Math.abs(item.contribution)), 1e-9);
  const rows = [...signal.contributions].reverse(); // newest first

  return (
    <div className="card">
      <div className="card-head">
        <h2 className="section-title">What moved the score</h2>
        <span className="card-note">weight × sentiment, newest first</span>
      </div>
      <div className="card-body">
        <div className="contrib">
          {rows.map((item) => {
            const share = (Math.abs(item.contribution) / largest) * 50;
            const positive = item.contribution >= 0;
            const label = headlines.get(item.normalized_event_id) ?? shortId(item.normalized_event_id);
            return (
              <div
                key={item.scored_event_id}
                className="contrib-row"
                role="button"
                tabIndex={0}
                onClick={() => onSelect(item.normalized_event_id)}
                onKeyDown={(key) => {
                  if (key.key === "Enter" || key.key === " ") {
                    key.preventDefault();
                    onSelect(item.normalized_event_id);
                  }
                }}
                aria-label={`${label}: contribution ${formatSigned(item.contribution, 3)}`}
              >
                <span className="contrib-label" title={label}>
                  {label}
                </span>
                <span className="contrib-track" aria-hidden="true">
                  <span
                    className="polarity-fill"
                    data-sign={positive ? "pos" : "neg"}
                    style={{ width: `${share}%`, top: 2, bottom: 2 }}
                  />
                </span>
                <span className="num" style={{ textAlign: "right" }}>
                  {formatSigned(item.contribution)}
                </span>
              </div>
            );
          })}
        </div>
        {signal.contributions_truncated ? (
          <p className="card-note" style={{ marginTop: 10 }}>
            Showing the {signal.contributions.length} largest contributions.
          </p>
        ) : null}
      </div>
    </div>
  );
}
