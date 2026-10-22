"use client";

import Link from "next/link";
import { useMemo, useState, type PointerEvent, type ReactNode } from "react";

import { formatDay, formatNumber, formatPercent, formatSigned } from "@/lib/format";
import type { EventItem, InstrumentOverview } from "@/lib/types";
import { useWidth } from "@/lib/useWidth";

import { StanceTag } from "./primitives";

const DAY_MS = 86_400_000;

/* ----------------------------------------------------------------- frame */

/** A chart card whose every chart has a table twin, so no value is hover-only. */
export function ChartCard({
  title,
  note,
  legend,
  table,
  children,
}: {
  title: string;
  note?: ReactNode;
  legend?: ReactNode;
  table?: ReactNode;
  children: ReactNode;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const showTable = view === "table" && table !== undefined;
  return (
    <section className="card chart-card">
      <div className="card-head">
        <div className="chart-card-titles">
          <h2 className="section-title">{title}</h2>
          {note ? <p className="card-note">{note}</p> : null}
        </div>
        {table !== undefined ? (
          <div className="segmented" role="group" aria-label={`${title}: view`}>
            <button type="button" aria-pressed={!showTable} onClick={() => setView("chart")}>
              Chart
            </button>
            <button type="button" aria-pressed={showTable} onClick={() => setView("table")}>
              Table
            </button>
          </div>
        ) : null}
      </div>
      <div className="card-body">
        {showTable ? (
          <div className="table-wrap">{table}</div>
        ) : (
          <>
            {legend ? <div className="legend chart-legend">{legend}</div> : null}
            {children}
          </>
        )}
      </div>
    </section>
  );
}

/* --------------------------------------------------------- classification */

export type Tone = "pos" | "neutral" | "neg" | "abstained" | "duplicate";

/** Same rule as the API's sentiment summary: only used events carry a polarity. */
export function toneOf(event: EventItem): Tone {
  if (event.duplicate_of) return "duplicate";
  const sentiment = event.sentiment;
  if (!sentiment || sentiment.abstained) return "abstained";
  if (sentiment.signed_sentiment > 0) return "pos";
  if (sentiment.signed_sentiment < 0) return "neg";
  return "neutral";
}

function dayKey(iso: string): string {
  return iso.slice(0, 10);
}

/** Every UTC day from the first to the last event, gaps included, so time reads true. */
export function dayRange(events: EventItem[]): string[] {
  if (events.length === 0) return [];
  const times = events.map((event) => Date.parse(`${dayKey(event.available_at)}T00:00:00Z`));
  const first = Math.min(...times);
  const last = Math.max(...times);
  const days: string[] = [];
  for (let at = first; at <= last; at += DAY_MS) days.push(new Date(at).toISOString().slice(0, 10));
  return days;
}

function dayLabel(day: string): string {
  return formatDay(`${day}T00:00:00Z`);
}

/* ---------------------------------------------------------------- tone mix */

const MIX: { tone: Exclude<Tone, "duplicate">; label: string }[] = [
  { tone: "pos", label: "Positive" },
  { tone: "neutral", label: "Neutral" },
  { tone: "neg", label: "Negative" },
  { tone: "abstained", label: "Abstained" },
];

/** Part-to-whole of every distinct news item by how the sentiment baseline read it. */
export function ToneMix({ events }: { events: EventItem[] }) {
  const counts = useMemo(() => {
    const tally: Record<Tone, number> = { pos: 0, neutral: 0, neg: 0, abstained: 0, duplicate: 0 };
    for (const event of events) tally[toneOf(event)] += 1;
    return tally;
  }, [events]);
  const total = MIX.reduce((sum, item) => sum + counts[item.tone], 0);

  return (
    <ChartCard
      title="Tone mix"
      note={`${formatNumber(total)} distinct items · ${formatNumber(counts.duplicate)} duplicates collapsed`}
      table={
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Reading</th>
              <th scope="col" className="col-num">Items</th>
              <th scope="col" className="col-num">Share</th>
            </tr>
          </thead>
          <tbody>
            {MIX.map(({ tone, label }) => (
              <tr key={tone}>
                <td>{label}</td>
                <td className="col-num num">{formatNumber(counts[tone])}</td>
                <td className="col-num num">{formatPercent(counts[tone], total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <div className="mix" role="img" aria-label={MIX.map(({ tone, label }) => `${label} ${counts[tone]}`).join(", ")}>
        <div className="mix-bar">
          {MIX.filter(({ tone }) => counts[tone] > 0).map(({ tone, label }) => (
            <span
              key={tone}
              className="mix-seg"
              data-tone={tone}
              style={{ flexGrow: counts[tone] }}
              title={`${label}: ${counts[tone]} (${formatPercent(counts[tone], total)})`}
            />
          ))}
        </div>
        <ul className="mix-legend">
          {MIX.map(({ tone, label }) => (
            <li key={tone}>
              <span className="swatch" data-tone={tone} aria-hidden="true" />
              <span className="mix-label">{label}</span>
              <span className="mix-count">{formatNumber(counts[tone])}</span>
              <span className="mix-share num">{formatPercent(counts[tone], total)}</span>
            </li>
          ))}
        </ul>
      </div>
    </ChartCard>
  );
}

/* ------------------------------------------------------------- leaderboard */

/** Latest decayed score per issuer on one shared zero baseline, strongest positive first. */
export function SignalLeaderboard({ instruments }: { instruments: InstrumentOverview[] }) {
  const rows = useMemo(
    () =>
      instruments
        .filter((item) => item.latest_signal !== null)
        .sort((a, b) => b.latest_signal!.score_normalized - a.latest_signal!.score_normalized),
    [instruments],
  );

  return (
    <ChartCard
      title="Signal leaderboard"
      note="Latest decayed news-tone score per issuer, −1 to +1. Unvalidated heuristic."
      table={
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Issuer</th>
              <th scope="col">Sector</th>
              <th scope="col" className="col-num">Score</th>
              <th scope="col">Stance</th>
              <th scope="col" className="col-num">Events</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ instrument, latest_signal, event_count }) => (
              <tr key={instrument.instrument_id}>
                <td>
                  <Link href={`/issuers/${instrument.symbol}`} className="mono">
                    {instrument.symbol}
                  </Link>{" "}
                  {instrument.name}
                </td>
                <td>{instrument.sector}</td>
                <td className="col-num num">{formatSigned(latest_signal!.score_normalized)}</td>
                <td>
                  <StanceTag signal={latest_signal!} />
                </td>
                <td className="col-num num">{event_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      {rows.length === 0 ? (
        <p className="muted">No signals yet.</p>
      ) : (
        <ol className="board">
          {rows.map(({ instrument, latest_signal }) => {
            const score = latest_signal!.score_normalized;
            const magnitude = `${Math.min(1, Math.abs(score)) * 50}%`;
            return (
              <li key={instrument.instrument_id}>
                <Link href={`/issuers/${instrument.symbol}`} className="board-row">
                  <span className="board-name">
                    <span className="mono board-symbol">{instrument.symbol}</span>
                    <span className="board-full">{instrument.name}</span>
                  </span>
                  <span className="board-track" aria-hidden="true">
                    <span
                      className="board-fill"
                      data-sign={score > 0 ? "pos" : score < 0 ? "neg" : "zero"}
                      data-abstained={latest_signal!.abstained}
                      style={score >= 0 ? { left: "50%", width: magnitude } : { right: "50%", width: magnitude }}
                    />
                  </span>
                  <span className="board-value num">{formatSigned(score)}</span>
                </Link>
              </li>
            );
          })}
        </ol>
      )}
      <div className="board-row board-axis-row" aria-hidden="true">
        <span />
        <span className="board-axis">
          <span>−1 negative</span>
          <span>0</span>
          <span>positive +1</span>
        </span>
        <span />
      </div>
    </ChartCard>
  );
}

/* --------------------------------------------------------------- daily flow */

interface DayCount {
  day: string;
  pos: number;
  neg: number;
  neutral: number;
  abstained: number;
}

function countByDay(events: EventItem[], days: string[]): DayCount[] {
  const byDay = new Map<string, DayCount>(
    days.map((day) => [day, { day, pos: 0, neg: 0, neutral: 0, abstained: 0 }]),
  );
  for (const event of events) {
    const tone = toneOf(event);
    const bucket = byDay.get(dayKey(event.available_at));
    if (!bucket || tone === "duplicate") continue;
    bucket[tone] += 1;
  }
  return days.map((day) => byDay.get(day)!);
}

/** Smallest "nice" step whose double covers `max`, so ticks land on round counts. */
function niceStep(max: number): number {
  for (const step of [1, 2, 5, 10, 20, 25, 50, 100, 200, 500]) if (step * 2 >= max) return step;
  return Math.ceil(max / 2);
}

const FLOW_HEIGHT = 230;
const FLOW_MARGIN = { top: 14, right: 8, bottom: 26, left: 34 };

/** Positive items rise above the baseline, negative items fall below it, per UTC day. */
export function DailyFlow({ events }: { events: EventItem[] }) {
  const [frame, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const days = useMemo(() => dayRange(events), [events]);
  const counts = useMemo(() => countByDay(events, days), [events, days]);

  const peak = Math.max(1, ...counts.map((item) => Math.max(item.pos, item.neg)));
  const step = niceStep(peak);
  const top = step * 2;
  const plotWidth = Math.max(0, width - FLOW_MARGIN.left - FLOW_MARGIN.right);
  const plotHeight = FLOW_HEIGHT - FLOW_MARGIN.top - FLOW_MARGIN.bottom;
  const zero = FLOW_MARGIN.top + plotHeight / 2;
  const half = plotHeight / 2 - 2;
  const band = counts.length ? plotWidth / counts.length : plotWidth;
  const barWidth = Math.max(4, Math.min(26, band * 0.56));
  const centre = (index: number) => FLOW_MARGIN.left + band * index + band / 2;
  const scale = (count: number) => (count / top) * half;
  const labelEvery = Math.max(1, Math.ceil(56 / Math.max(1, band)));

  function onMove(event: PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const index = Math.floor((event.clientX - bounds.left - FLOW_MARGIN.left) / band);
    setHover(index >= 0 && index < counts.length ? index : null);
  }

  const hovered = hover === null ? null : counts[hover];
  const tooltipStyle =
    hover === null
      ? undefined
      : centre(hover) > width / 2
        ? { right: width - centre(hover) + barWidth, top: FLOW_MARGIN.top }
        : { left: centre(hover) + barWidth, top: FLOW_MARGIN.top };

  return (
    <ChartCard
      title="Daily news flow"
      note="Distinct items by the day they became usable (UTC). Positive above the line, negative below."
      legend={
        <>
          <span className="legend-item">
            <span className="swatch" data-tone="pos" aria-hidden="true" /> Positive
          </span>
          <span className="legend-item">
            <span className="swatch" data-tone="neg" aria-hidden="true" /> Negative
          </span>
        </>
      }
      table={
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Day (UTC)</th>
              <th scope="col" className="col-num">Positive</th>
              <th scope="col" className="col-num">Negative</th>
              <th scope="col" className="col-num">Neutral</th>
              <th scope="col" className="col-num">Abstained</th>
            </tr>
          </thead>
          <tbody>
            {counts.map((item) => (
              <tr key={item.day}>
                <td>{dayLabel(item.day)}</td>
                <td className="col-num num">{item.pos}</td>
                <td className="col-num num">{item.neg}</td>
                <td className="col-num num">{item.neutral}</td>
                <td className="col-num num">{item.abstained}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <div className="chart" ref={frame}>
        {width > 0 && counts.length > 0 ? (
          <svg
            height={FLOW_HEIGHT}
            role="img"
            aria-label={`Daily news flow over ${counts.length} days. Use the table view for exact counts.`}
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
          >
            {[top, step, 0, -step, -top].map((tick) => {
              const y = zero - Math.sign(tick) * scale(Math.abs(tick));
              return (
                <g key={tick}>
                  <line
                    className={tick === 0 ? "chart-zero" : "chart-grid"}
                    x1={FLOW_MARGIN.left}
                    x2={FLOW_MARGIN.left + plotWidth}
                    y1={y}
                    y2={y}
                  />
                  <text className="chart-tick" x={FLOW_MARGIN.left - 8} y={y} dy="0.32em" textAnchor="end">
                    {Math.abs(tick)}
                  </text>
                </g>
              );
            })}
            {hover !== null ? (
              <rect
                className="chart-hover-band"
                x={FLOW_MARGIN.left + band * hover}
                width={band}
                y={FLOW_MARGIN.top}
                height={plotHeight}
              />
            ) : null}
            {counts.map((item, index) => {
              const x = centre(index) - barWidth / 2;
              const up = scale(item.pos);
              const down = scale(item.neg);
              const r = Math.min(4, barWidth / 2);
              return (
                <g key={item.day}>
                  {up > 0 ? <path className="flow-bar" data-sign="pos" d={roundedUp(x, zero - 1, barWidth, up, r)} /> : null}
                  {down > 0 ? <path className="flow-bar" data-sign="neg" d={roundedDown(x, zero + 1, barWidth, down, r)} /> : null}
                  {index % labelEvery === 0 ? (
                    <text className="chart-tick" x={centre(index)} y={FLOW_HEIGHT - 8} textAnchor="middle">
                      {dayLabel(item.day)}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </svg>
        ) : (
          <div style={{ height: FLOW_HEIGHT }} />
        )}
        {hovered ? (
          <div className="tooltip" style={tooltipStyle} role="presentation">
            <div className="tooltip-value">{dayLabel(hovered.day)}</div>
            <p className="tooltip-text">
              <span className="swatch" data-tone="pos" aria-hidden="true" /> {hovered.pos} positive ·{" "}
              <span className="swatch" data-tone="neg" aria-hidden="true" /> {hovered.neg} negative
            </p>
            <p className="tooltip-meta">
              {hovered.neutral} neutral · {hovered.abstained} abstained
            </p>
          </div>
        ) : null}
      </div>
    </ChartCard>
  );
}

/** A bar rising from `baseY`, rounded only at its free (top) end. */
function roundedUp(x: number, baseY: number, w: number, h: number, r: number): string {
  const rr = Math.min(r, h);
  return `M${x} ${baseY}V${baseY - h + rr}Q${x} ${baseY - h} ${x + rr} ${baseY - h}H${x + w - rr}Q${x + w} ${baseY - h} ${x + w} ${baseY - h + rr}V${baseY}Z`;
}

/** A bar falling from `baseY`, rounded only at its free (bottom) end. */
function roundedDown(x: number, baseY: number, w: number, h: number, r: number): string {
  const rr = Math.min(r, h);
  return `M${x} ${baseY}V${baseY + h - rr}Q${x} ${baseY + h} ${x + rr} ${baseY + h}H${x + w - rr}Q${x + w} ${baseY + h} ${x + w} ${baseY + h - rr}V${baseY}Z`;
}

/* ----------------------------------------------------------------- heatmap */

interface Cell {
  mean: number | null;
  used: number;
}

/** Diverging fill: the pole hue mixed into the neutral surface by magnitude. */
function cellColor(cell: Cell): string | undefined {
  if (cell.mean === null) return undefined;
  if (cell.mean === 0) return "color-mix(in oklab, var(--mark-muted) 40%, var(--surface-2))";
  const strength = Math.round(22 + 78 * Math.min(1, Math.abs(cell.mean)));
  const pole = cell.mean > 0 ? "var(--pos)" : "var(--neg)";
  return `color-mix(in oklab, ${pole} ${strength}%, var(--surface-2))`;
}

/** Issuer × day grid of mean signed sentiment over the events the score could use. */
export function SentimentHeatmap({
  events,
  instruments,
}: {
  events: EventItem[];
  instruments: InstrumentOverview[];
}) {
  const days = useMemo(() => dayRange(events), [events]);
  const [hover, setHover] = useState<{ row: number; col: number; x: number; y: number } | null>(null);

  const rows = useMemo(() => {
    const sums = new Map<string, Map<string, { total: number; used: number }>>();
    for (const event of events) {
      if (toneOf(event) === "duplicate" || toneOf(event) === "abstained") continue;
      const day = dayKey(event.available_at);
      for (const ref of event.instruments) {
        const byDay = sums.get(ref.instrument_id) ?? new Map();
        const slot = byDay.get(day) ?? { total: 0, used: 0 };
        slot.total += event.sentiment!.signed_sentiment;
        slot.used += 1;
        byDay.set(day, slot);
        sums.set(ref.instrument_id, byDay);
      }
    }
    return instruments
      .filter((item) => sums.has(item.instrument.instrument_id))
      .map((item) => {
        const byDay = sums.get(item.instrument.instrument_id)!;
        const cells: Cell[] = days.map((day) => {
          const slot = byDay.get(day);
          return slot ? { mean: slot.total / slot.used, used: slot.used } : { mean: null, used: 0 };
        });
        const used = cells.filter((cell) => cell.mean !== null);
        const overall = used.reduce((sum, cell) => sum + cell.mean! * cell.used, 0) /
          Math.max(1, used.reduce((sum, cell) => sum + cell.used, 0));
        return { instrument: item.instrument, cells, overall };
      })
      .sort((a, b) => b.overall - a.overall);
  }, [events, instruments, days]);

  const hovered = hover ? { row: rows[hover.row], cell: rows[hover.row]?.cells[hover.col], day: days[hover.col] } : null;

  return (
    <ChartCard
      title="Sentiment heatmap"
      note="Mean signed sentiment of usable items per issuer and UTC day. Rows sorted by overall tone."
      legend={
        <>
          <span className="legend-item heat-scale-item">
            <span className="mono num">−1</span>
            <span className="heat-scale" aria-hidden="true" />
            <span className="mono num">+1</span>
          </span>
          <span className="legend-item">
            <span className="heat-empty-swatch" aria-hidden="true" /> No usable news
          </span>
        </>
      }
      table={
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Issuer</th>
              {days.map((day) => (
                <th key={day} scope="col" className="col-num">
                  {dayLabel(day)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.instrument.instrument_id}>
                <th scope="row" className="mono">
                  {row.instrument.symbol}
                </th>
                {row.cells.map((cell, index) => (
                  <td key={days[index]} className="col-num num">
                    {cell.mean === null ? "—" : formatSigned(cell.mean)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <div className="heat-scroll">
        <div
          className="heat"
          style={{ gridTemplateColumns: `minmax(64px, 88px) repeat(${days.length}, minmax(22px, 1fr))` }}
          role="img"
          aria-label={`Sentiment heatmap for ${rows.length} issuers over ${days.length} days. Use the table view for exact values.`}
          onPointerLeave={() => setHover(null)}
        >
          {rows.map((row, rowIndex) => (
            <div key={row.instrument.instrument_id} className="heat-row">
              <Link href={`/issuers/${row.instrument.symbol}`} className="heat-label mono">
                {row.instrument.symbol}
              </Link>
              {row.cells.map((cell, colIndex) => (
                <span
                  key={days[colIndex]}
                  className="heat-cell"
                  data-empty={cell.mean === null}
                  data-active={hover?.row === rowIndex && hover.col === colIndex}
                  style={{ background: cellColor(cell) }}
                  onPointerEnter={(event) => {
                    const box = event.currentTarget;
                    setHover({
                      row: rowIndex,
                      col: colIndex,
                      x: box.offsetLeft + box.offsetWidth / 2,
                      y: box.offsetTop,
                    });
                  }}
                />
              ))}
            </div>
          ))}
          <div className="heat-row heat-axis" aria-hidden="true">
            <span />
            {days.map((day, index) => (
              <span key={day} className="chart-tick-html">
                {index % Math.max(1, Math.ceil(days.length / 8)) === 0 ? dayLabel(day) : ""}
              </span>
            ))}
          </div>
        </div>
        {hovered?.row && hovered.cell && hover ? (
          <div
            className="tooltip"
            role="presentation"
            style={{ left: hover.x, top: hover.y, transform: "translate(-50%, calc(-100% - 8px))" }}
          >
            <div className="tooltip-value">
              <span className="mono">{hovered.row.instrument.symbol}</span>
              <span className="num">{hovered.cell.mean === null ? "no usable news" : formatSigned(hovered.cell.mean)}</span>
            </div>
            <p className="tooltip-meta">
              {dayLabel(hovered.day!)} · {hovered.cell.used} usable {hovered.cell.used === 1 ? "item" : "items"}
            </p>
          </div>
        ) : null}
      </div>
    </ChartCard>
  );
}

/* ------------------------------------------------------------------ funnel */

export interface FunnelStage {
  name: string;
  topic: string;
  value: number;
  drop?: { label: string; value: number };
}

/** Records that reached each stage, on one shared scale so attrition reads as length. */
export function Funnel({ stages }: { stages: FunnelStage[] }) {
  const max = Math.max(1, ...stages.map((stage) => stage.value));
  return (
    <ol className="funnel">
      {stages.map((stage, index) => (
        <li key={stage.name} className="funnel-row">
          <span className="funnel-step mono">{String(index + 1).padStart(2, "0")}</span>
          <span className="funnel-name">
            <strong>{stage.name}</strong>
            <span className="mono funnel-topic">{stage.topic}</span>
          </span>
          <span className="funnel-track" aria-hidden="true">
            <span className="funnel-fill" style={{ width: `${(stage.value / max) * 100}%` }} />
          </span>
          <span className="funnel-value">{formatNumber(stage.value)}</span>
          <span className="funnel-drop">
            {stage.drop && stage.drop.value > 0
              ? `${formatNumber(stage.drop.value)} ${stage.drop.label}`
              : ""}
          </span>
        </li>
      ))}
    </ol>
  );
}
