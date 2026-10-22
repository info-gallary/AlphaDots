"use client";

import Link from "next/link";

import { formatClock, formatDay, formatDuration, humanize, sourceName } from "@/lib/format";
import type { EventItem } from "@/lib/types";

import { Dot, Meter, PolarityBar, StatusTag } from "./primitives";

const LATE_ARRIVAL_SECONDS = 30 * 60;

function Tags({ event }: { event: EventItem }) {
  const tags = [];
  if (event.duplicate_of) {
    tags.push(<StatusTag key="dup" tone="neutral">Duplicate</StatusTag>);
  }
  for (const flag of event.resolution_flags) {
    tags.push(
      <StatusTag key={flag} tone={flag === "multi_issuer" ? "neutral" : "warning"}>
        {humanize(flag)}
      </StatusTag>,
    );
  }
  if (event.source_latency_seconds > LATE_ARRIVAL_SECONDS) {
    tags.push(
      <StatusTag key="late" tone="warning">
        Late by {formatDuration(event.source_latency_seconds)}
      </StatusTag>,
    );
  }
  return tags.length ? <div className="tape-tags">{tags}</div> : null;
}

function Polarity({ event }: { event: EventItem }) {
  const sentiment = event.sentiment;
  if (!sentiment) return <span className="muted">pending</span>;
  if (sentiment.abstained) {
    return (
      <span className="stance" title={humanize(sentiment.abstain_reason ?? "")}>
        <Dot sign="zero" hollow />
        abstained
      </span>
    );
  }
  return <PolarityBar value={sentiment.signed_sentiment} />;
}

export function EventTable({
  events,
  onSelect,
  caption,
}: {
  events: EventItem[];
  onSelect: (event: EventItem) => void;
  caption: string;
}) {
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Available (UTC)</th>
            <th scope="col" className="col-num">
              Latency
            </th>
            <th scope="col">Source</th>
            <th scope="col">Issuer</th>
            <th scope="col">Headline</th>
            <th scope="col">Type</th>
            <th scope="col">Polarity</th>
            <th scope="col">Uncertainty</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr
              key={event.event_id}
              className="tape-row"
              data-dim={Boolean(event.duplicate_of)}
              tabIndex={0}
              aria-label={`Open provenance for: ${event.headline}`}
              onClick={() => onSelect(event)}
              onKeyDown={(key) => {
                if (key.key === "Enter" || key.key === " ") {
                  key.preventDefault();
                  onSelect(event);
                }
              }}
            >
              <td className="tape-time mono num">
                {formatClock(event.available_at)}
                <span className="tape-day">{formatDay(event.available_at)}</span>
              </td>
              <td className="col-num num muted" title="first seen minus published">
                {formatDuration(event.source_latency_seconds)}
              </td>
              <td className="muted" style={{ whiteSpace: "nowrap" }}>
                {sourceName(event.source)}
              </td>
              <td>
                <div className="instruments">
                  {event.instruments.length ? (
                    event.instruments.map((ref) => (
                      <Link
                        key={ref.instrument_id}
                        href={`/issuers/${ref.symbol}`}
                        className="chip chip-solid chip-link mono"
                        title={`Link confidence ${ref.confidence.toFixed(2)}`}
                        onClick={(click) => click.stopPropagation()}
                      >
                        {ref.symbol}
                        {ref.confidence < 0.9 ? (
                          <span className="conf">{ref.confidence.toFixed(2)}</span>
                        ) : null}
                      </Link>
                    ))
                  ) : (
                    <span className="muted">none</span>
                  )}
                </div>
              </td>
              <td>
                <div className="tape-headline">{event.headline}</div>
                <Tags event={event} />
              </td>
              <td className="muted" style={{ whiteSpace: "nowrap" }}>
                {event.sentiment ? humanize(event.sentiment.event_type) : ""}
              </td>
              <td>
                <Polarity event={event} />
              </td>
              <td>
                {event.sentiment && !event.sentiment.abstained ? (
                  <Meter value={event.sentiment.entropy} />
                ) : (
                  <span className="muted">n/a</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
