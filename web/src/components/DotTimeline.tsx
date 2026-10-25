"use client";

import { useMemo, useState, type PointerEvent } from "react";

import { formatDay, formatMinute, formatSigned, humanize } from "@/lib/format";
import type { SentimentPoint, SignalPoint } from "@/lib/types";
import { useWidth } from "@/lib/useWidth";

import { Dot, signOf } from "./primitives";

const HEIGHT = 300;
const MARGIN = { top: 14, right: 16, bottom: 28, left: 40 };
const Y_TICKS = [-1, -0.5, 0, 0.5, 1];
const HIT_RADIUS = 28;
const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const TICK_STEPS = [HOUR, 2 * HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, 7 * DAY, 14 * DAY, 30 * DAY, 61 * DAY];

type Hover =
  | { kind: "dot"; point: SentimentPoint; x: number; y: number }
  | { kind: "line"; signal: SignalPoint | null; time: number; x: number; y: number };

function timeTicks(start: number, end: number, maxTicks: number): number[] {
  const span = end - start;
  const step = TICK_STEPS.find((candidate) => span / candidate <= maxTicks) ?? 91 * DAY;
  const first = Math.ceil(start / step) * step;
  const ticks = [];
  for (let tick = first; tick <= end; tick += step) ticks.push(tick);
  return ticks;
}

function tickLabel(ms: number, step: number): string {
  const iso = new Date(ms).toISOString();
  if (step >= 24 * HOUR || new Date(ms).getUTCHours() === 0) return formatDay(iso);
  return iso.slice(11, 16);
}

/** Dot radius from the event's weight in the aggregate: relevance × materiality. */
function radius(point: SentimentPoint): number {
  const used = !point.abstained && !point.is_duplicate;
  return used ? 4 + 4 * Math.sqrt(Math.max(0, point.relevance * point.materiality)) : 4;
}

/**
 * Sentiment events as dots over time, with the decayed heuristic score as a step line.
 *
 * Both share one axis because both are on the same scale: signed sentiment in [-1, 1].
 * Filled dots are events the score uses; hollow dots were abstained or duplicate and
 * contribute nothing. The step line holds each emitted score until the next one.
 */
export function DotTimeline({
  points,
  signals,
  start,
  end,
  threshold,
  onSelect,
}: {
  points: SentimentPoint[];
  signals: SignalPoint[];
  start: number;
  end: number;
  threshold: number;
  onSelect: (eventId: string) => void;
}) {
  const [frame, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<Hover | null>(null);
  const plotWidth = Math.max(0, width - MARGIN.left - MARGIN.right);
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const span = Math.max(1, end - start);

  const x = (ms: number) => MARGIN.left + ((ms - start) / span) * plotWidth;
  const y = (value: number) => MARGIN.top + ((1 - value) / 2) * plotHeight;

  const dots = useMemo(
    () =>
      points
        .map((point) => ({ point, at: Date.parse(point.available_at), r: radius(point) }))
        // Larger marks first, so small ones stay visible on top.
        .sort((a, b) => b.r - a.r),
    [points],
  );

  // Signals in time order, starting from the last one emitted before the window opened.
  const steps = useMemo(() => {
    const ordered = signals
      .map((signal) => ({ signal, at: Date.parse(signal.as_of) }))
      .filter((item) => item.at <= end)
      .sort((a, b) => a.at - b.at);
    const before = ordered.filter((item) => item.at < start);
    const inside = ordered.filter((item) => item.at >= start);
    const carried = before[before.length - 1];
    return carried ? [{ signal: carried.signal, at: start }, ...inside] : inside;
  }, [signals, start, end]);

  // Consecutive steps with the same abstention state share one path.
  const runs = useMemo(() => {
    const result: { abstained: boolean; d: string }[] = [];
    steps.forEach((step, index) => {
      const next = steps[index + 1];
      const x0 = x(step.at);
      const x1 = x(next ? next.at : end);
      const y0 = y(step.signal.score_normalized);
      const segment = `M${x0.toFixed(1)} ${y0.toFixed(1)}H${x1.toFixed(1)}`;
      const connector = next ? `V${y(next.signal.score_normalized).toFixed(1)}` : "";
      const last = result[result.length - 1];
      if (last && last.abstained === step.signal.abstained) last.d += `${segment}${connector}`;
      else result.push({ abstained: step.signal.abstained, d: `${segment}${connector}` });
    });
    return result;
    // x and y are derived from the dependencies below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [steps, start, end, plotWidth]);

  const ticks = timeTicks(start, end, Math.max(2, Math.floor(plotWidth / 92)));
  const tickStep = ticks.length > 1 ? ticks[1]! - ticks[0]! : 24 * HOUR;

  function signalAt(time: number): SignalPoint | null {
    let found: SignalPoint | null = null;
    for (const step of steps) {
      if (step.at <= time) found = step.signal;
      else break;
    }
    return found;
  }

  function onMove(event: PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const px = event.clientX - bounds.left;
    const py = event.clientY - bounds.top;
    if (px < MARGIN.left || px > MARGIN.left + plotWidth) {
      setHover(null);
      return;
    }
    let best: Hover | null = null;
    let bestDistance = HIT_RADIUS;
    for (const dot of dots) {
      const dx = x(dot.at) - px;
      const dy = y(dot.point.signed_sentiment) - py;
      const distance = Math.hypot(dx, dy);
      if (distance <= Math.max(bestDistance, dot.r)) {
        bestDistance = distance;
        best = { kind: "dot", point: dot.point, x: x(dot.at), y: y(dot.point.signed_sentiment) };
      }
    }
    if (best) {
      setHover(best);
      return;
    }
    const time = start + ((px - MARGIN.left) / plotWidth) * span;
    const signal = signalAt(time);
    setHover({ kind: "line", signal, time, x: px, y: signal ? y(signal.score_normalized) : py });
  }

  const tooltipStyle = hover
    ? hover.x > width / 2
      ? { right: width - hover.x + 14, top: Math.max(0, hover.y - 24) }
      : { left: hover.x + 14, top: Math.max(0, hover.y - 24) }
    : undefined;

  const used = points.filter((point) => !point.abstained && !point.is_duplicate).length;
  const summary = `${points.length} scored events, ${used} used by the score, from ${formatMinute(
    new Date(start).toISOString(),
  )} to ${formatMinute(new Date(end).toISOString())} UTC. The table below lists every event.`;

  return (
    <div className="chart" ref={frame}>
      {width > 0 ? (
        <svg
          height={HEIGHT}
          role="img"
          aria-label={`Sentiment timeline. ${summary}`}
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
          onClick={() => {
            if (hover?.kind === "dot") onSelect(hover.point.event_id);
          }}
          style={{ cursor: hover?.kind === "dot" ? "pointer" : "crosshair" }}
        >
          <rect
            className="chart-band"
            x={MARGIN.left}
            width={plotWidth}
            y={y(threshold)}
            height={y(-threshold) - y(threshold)}
          />
          {Y_TICKS.map((tick) => (
            <g key={tick}>
              <line
                className={tick === 0 ? "chart-zero" : "chart-grid"}
                x1={MARGIN.left}
                x2={MARGIN.left + plotWidth}
                y1={y(tick)}
                y2={y(tick)}
              />
              <text className="chart-tick" x={MARGIN.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">
                {tick === 0 ? "0" : formatSigned(tick, 1)}
              </text>
            </g>
          ))}
          {ticks.map((tick) => (
            <text
              key={tick}
              className="chart-tick"
              x={x(tick)}
              y={HEIGHT - 8}
              textAnchor="middle"
            >
              {tickLabel(tick, tickStep)}
            </text>
          ))}

          {runs.map((run, index) => (
            <path key={index} className="chart-line" data-abstained={run.abstained} d={run.d} />
          ))}

          {hover?.kind === "line" ? (
            <line
              className="chart-cross"
              x1={hover.x}
              x2={hover.x}
              y1={MARGIN.top}
              y2={MARGIN.top + plotHeight}
            />
          ) : null}

          {dots.map(({ point, at, r }) => {
            const isHovered = hover?.kind === "dot" && hover.point.event_id === point.event_id;
            const hollow = point.abstained || point.is_duplicate;
            return (
              <circle
                key={point.event_id}
                className="chart-dot"
                data-sign={signOf(point.signed_sentiment)}
                data-hollow={hollow}
                cx={x(at)}
                cy={y(point.signed_sentiment)}
                r={isHovered ? r + 2 : r}
              />
            );
          })}
        </svg>
      ) : (
        <div style={{ height: HEIGHT }} />
      )}

      {hover ? (
        <div className="tooltip" style={tooltipStyle} role="presentation">
          {hover.kind === "dot" ? (
            <>
              <div className="tooltip-value">
                <Dot
                  sign={signOf(hover.point.signed_sentiment)}
                  hollow={hover.point.abstained || hover.point.is_duplicate}
                />
                <span className="num">{formatSigned(hover.point.signed_sentiment)}</span>
                <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>
                  signed sentiment
                </span>
              </div>
              <p className="tooltip-text">{hover.point.headline}</p>
              <p className="tooltip-meta">
                {formatMinute(hover.point.available_at)} UTC · {humanize(hover.point.event_type)} · weight{" "}
                {(hover.point.relevance * hover.point.materiality).toFixed(2)}
                {hover.point.is_duplicate
                  ? " · duplicate, excluded"
                  : hover.point.abstained
                    ? ` · abstained (${humanize(hover.point.abstain_reason ?? "")})`
                    : ""}
              </p>
            </>
          ) : (
            <>
              <div className="tooltip-value">
                <span className="legend-line" aria-hidden="true" />
                <span className="num">
                  {hover.signal ? formatSigned(hover.signal.score_normalized) : "none yet"}
                </span>
                <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>
                  decayed score
                </span>
              </div>
              <p className="tooltip-meta">
                {formatMinute(new Date(hover.time).toISOString())} UTC
                {hover.signal?.abstained ? " · score abstained: too little evidence" : ""}
              </p>
            </>
          )}
        </div>
      ) : null}
      <p className="visually-hidden">{summary}</p>
    </div>
  );
}
