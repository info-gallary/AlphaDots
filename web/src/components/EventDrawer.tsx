"use client";

import { useEffect, useRef } from "react";

import { formatDuration, formatTime, humanize, shortId, sourceName } from "@/lib/format";
import type { EventDetailResponse, EventItem } from "@/lib/types";
import { useApi } from "@/lib/useApi";

import { useAppState } from "./AppState";
import { CloseGlyph } from "./icons";
import { Dot, signOf, StatusTag } from "./primitives";

/** Fixture URLs live under the reserved `.invalid` TLD and cannot be opened. */
function isNavigable(url: string): boolean {
  try {
    const parsed = new URL(url);
    return (parsed.protocol === "https:" || parsed.protocol === "http:") && !parsed.hostname.endsWith(".invalid");
  } catch {
    return false;
  }
}

function Timeline({ event }: { event: EventItem }) {
  const steps = [
    {
      label: "Published",
      help: "When the source says it published. A claim, not something we observed.",
      at: event.published_at,
    },
    { label: "First seen", help: "When AlphaDots first observed the item.", at: event.first_seen_at },
    { label: "Ingested", help: "Accepted onto the event bus.", at: event.ingested_at },
    {
      label: "Event available",
      help: "Normalised and linked to issuers; usable from here.",
      at: event.available_at,
      key: true,
    },
    ...(event.sentiment
      ? [
          {
            label: "Score available",
            help: "Sentiment scored. A signal cannot use it before this moment.",
            at: event.sentiment.available_at,
            key: true,
          },
        ]
      : []),
  ];
  return (
    <ol className="timeline">
      {steps.map((step, index) => {
        const previous = index > 0 ? steps[index - 1] : undefined;
        const delta = previous ? (Date.parse(step.at) - Date.parse(previous.at)) / 1000 : null;
        return (
          <li key={step.label} className="timeline-step" data-key={Boolean(step.key)}>
            <span className="timeline-node" aria-hidden="true" />
            <span className="timeline-label">{step.label}</span>
            <span className="timeline-time mono num">
              {formatTime(step.at)}
              {delta !== null ? <span className="muted"> · +{formatDuration(delta)}</span> : null}
            </span>
            <span className="timeline-help">{step.help}</span>
          </li>
        );
      })}
    </ol>
  );
}

function SentimentDetail({ event }: { event: EventItem }) {
  const sentiment = event.sentiment;
  if (!sentiment) {
    return <p className="muted">No score is available as of the selected time.</p>;
  }
  const { scores } = sentiment;
  return (
    <>
      <div className="scorebar" aria-hidden="true">
        <span data-part="neg" style={{ flex: scores.negative }} />
        <span data-part="neu" style={{ flex: scores.neutral }} />
        <span data-part="pos" style={{ flex: scores.positive }} />
      </div>
      <div className="legend" style={{ marginBottom: 12 }}>
        <span className="legend-item">
          <Dot sign="neg" /> Negative {scores.negative.toFixed(2)}
        </span>
        <span className="legend-item">
          <Dot sign="zero" /> Neutral {scores.neutral.toFixed(2)}
        </span>
        <span className="legend-item">
          <Dot sign="pos" /> Positive {scores.positive.toFixed(2)}
        </span>
      </div>
      <dl className="kv">
        <dt>Outcome</dt>
        <dd>
          {sentiment.abstained ? (
            <StatusTag tone="neutral">Abstained: {humanize(sentiment.abstain_reason ?? "")}</StatusTag>
          ) : (
            <span className="num">
              {sentiment.signed_sentiment > 0 ? "+" : ""}
              {sentiment.signed_sentiment.toFixed(2)} signed · entropy {sentiment.entropy.toFixed(2)}
            </span>
          )}
        </dd>
        <dt>Matched terms</dt>
        <dd>
          {sentiment.rationale_terms.length ? (
            <span className="tape-tags" style={{ marginTop: 0 }}>
              {sentiment.rationale_terms.map((term, index) => (
                <span key={`${term}-${index}`} className="chip">
                  <Dot sign={term.startsWith("+") ? "pos" : "neg"} />
                  {term.slice(1)}
                </span>
              ))}
            </span>
          ) : (
            <span className="muted">None</span>
          )}
        </dd>
        <dt>Event type</dt>
        <dd>
          {humanize(sentiment.event_type)} · materiality prior {sentiment.materiality.toFixed(1)}
        </dd>
        <dt>Novelty</dt>
        <dd>{sentiment.novelty === 0 ? "0 (exact copy of an earlier item)" : "1 (first of its kind)"}</dd>
        <dt>Model</dt>
        <dd className="mono">
          {sentiment.model_id}@{sentiment.model_version}
        </dd>
        <dt>Calibration</dt>
        <dd>
          {sentiment.calibration_version === "uncalibrated"
            ? "Uncalibrated: these are scores, not probabilities"
            : sentiment.calibration_version}
        </dd>
      </dl>
    </>
  );
}

export function EventDrawer({ event, onClose }: { event: EventItem | null; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const { asOf } = useAppState();
  const detail = useApi<EventDetailResponse>(event ? `v1/events/${event.event_id}` : null, {
    as_of: asOf,
  });

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (event && !element.open) element.showModal();
    if (!event && element.open) element.close();
  }, [event]);

  // Show the row we were given at once; switch to the detail payload when it arrives.
  const fresh = detail.data && detail.data.data.event_id === event?.event_id ? detail.data : null;
  const shown = fresh ? fresh.data : event;
  const cluster = fresh ? fresh.cluster : [];

  return (
    <dialog
      ref={dialog}
      className="drawer"
      aria-labelledby="drawer-title"
      onClose={onClose}
      onClick={(click) => {
        if (click.target === dialog.current) onClose(); // backdrop
      }}
    >
      {shown ? (
        <>
          <div className="drawer-head">
            <div>
              <p className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
                {sourceName(shown.source)} · {shown.source_event_id}
              </p>
              <h2 id="drawer-title" className="drawer-title">
                {shown.headline}
              </h2>
            </div>
            <button type="button" className="btn btn-ghost" onClick={onClose} aria-label="Close">
              <CloseGlyph />
            </button>
          </div>
          <div className="drawer-body">
            {shown.body ? <p className="drawer-text">{shown.body}</p> : null}

            <section className="drawer-section">
              <h3>When it became usable</h3>
              <Timeline event={shown} />
            </section>

            <section className="drawer-section">
              <h3>Issuer links</h3>
              {shown.instruments.length ? (
                <dl className="kv">
                  {shown.instruments.map((ref) => (
                    <div key={ref.instrument_id} style={{ display: "contents" }}>
                      <dt className="mono">{ref.symbol}</dt>
                      <dd>
                        confidence {ref.confidence.toFixed(2)} · <span className="mono">{ref.instrument_id}</span>
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="muted">No tradable issuer could be identified.</p>
              )}
              {shown.resolution_flags.length ? (
                <p className="tape-tags">
                  {shown.resolution_flags.map((flag) => (
                    <StatusTag key={flag} tone="warning">
                      {humanize(flag)}
                    </StatusTag>
                  ))}
                </p>
              ) : null}
            </section>

            <section className="drawer-section">
              <h3>Sentiment (baseline model)</h3>
              <SentimentDetail event={shown} />
            </section>

            <section className="drawer-section">
              <h3>Provenance</h3>
              <dl className="kv">
                <dt>Source URL</dt>
                <dd className="mono">
                  {isNavigable(shown.source_url) ? (
                    <a href={shown.source_url} target="_blank" rel="noopener noreferrer nofollow">
                      {shown.source_url}
                    </a>
                  ) : (
                    <>
                      {shown.source_url}
                      <span className="muted" style={{ fontFamily: "var(--font-sans)" }}>
                        {" "}
                        (synthetic, not a real page)
                      </span>
                    </>
                  )}
                </dd>
                <dt>Licence</dt>
                <dd>{shown.source_license}</dd>
                <dt>Raw payload</dt>
                <dd className="mono">{shown.payload_ref}</dd>
                <dt>Content hash</dt>
                <dd className="mono">{shown.content_hash.slice(0, 24)}…</dd>
                <dt>Event ID</dt>
                <dd className="mono">{shown.event_id}</dd>
                <dt>Trace ID</dt>
                <dd className="mono">{shown.trace_id}</dd>
                <dt>Normaliser</dt>
                <dd className="mono">{shown.normalizer_version}</dd>
                <dt>Language</dt>
                <dd>{shown.language}</dd>
              </dl>
            </section>

            {cluster.length > 1 ? (
              <section className="drawer-section">
                <h3>Duplicate cluster</h3>
                <dl className="kv">
                  {cluster.map((member) => (
                    <div key={member.event_id} style={{ display: "contents" }}>
                      <dt>{member.is_canonical ? "Canonical" : "Copy"}</dt>
                      <dd>
                        <Dot sign={member.is_canonical ? signOf(1) : "zero"} hollow={!member.is_canonical} />{" "}
                        {sourceName(member.source)} · seen {formatTime(member.first_seen_at)} ·{" "}
                        <span className="mono">{shortId(member.event_id)}</span>
                      </dd>
                    </div>
                  ))}
                </dl>
                <p className="card-note" style={{ marginTop: 8 }}>
                  Copies are kept for provenance but never counted twice by a signal.
                </p>
              </section>
            ) : null}
          </div>
        </>
      ) : null}
    </dialog>
  );
}
