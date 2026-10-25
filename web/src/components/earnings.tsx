"use client";

import { useState, type PointerEvent } from "react";

import { bps, earnings, type Interval } from "@/lib/earnings";
import { formatNumber, formatSigned } from "@/lib/format";
import { useWidth } from "@/lib/useWidth";

import { pct, SeriesSwatch, ticks } from "./backtest";
import { ChartCard } from "./charts";

/** Tone thirds in the order the study lists them: most negative, middle, most positive. */
const THIRD_TONE = ["neg", "neutral", "pos"] as const;
const THIRD_SHORT = ["Negative", "Middle", "Positive"];

function ThirdSwatch({ index }: { index: number }) {
  return <span className="swatch swatch-line" data-tone={THIRD_TONE[index]} aria-hidden="true" />;
}

/** Nudge right-edge labels apart so none overlap. */
function spread(ys: number[], gap: number): number[] {
  const order = ys.map((y, index) => ({ y, index })).sort((a, b) => a.y - b.y);
  for (let i = 1; i < order.length; i += 1) {
    if (order[i]!.y - order[i - 1]!.y < gap) order[i]!.y = order[i - 1]!.y + gap;
  }
  const out = [...ys];
  for (const item of order) out[item.index] = item.y;
  return out;
}

/* --------------------------------------------------------- event-time paths */

const PATH_HEIGHT = 340;
const PATH_MARGIN = { top: 26, right: 92, bottom: 38, left: 56 };

export function CarPathChart() {
  const { offsets, groups } = earnings.car_paths;
  const [drift0, drift1] = earnings.plan.drift;
  const [frame, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const values = groups.flatMap((group) => [...group.low, ...group.high]);
  const pad = (Math.max(...values) - Math.min(...values)) * 0.06 || 0.001;
  const min = Math.min(0, ...values) - pad;
  const max = Math.max(0, ...values) + pad;
  const first = offsets[0]!;
  const last = offsets[offsets.length - 1]!;
  const plotWidth = Math.max(0, width - PATH_MARGIN.left - PATH_MARGIN.right);
  const plotHeight = PATH_HEIGHT - PATH_MARGIN.top - PATH_MARGIN.bottom;
  const x = (offset: number) => PATH_MARGIN.left + ((offset - first) / (last - first)) * plotWidth;
  const y = (value: number) => PATH_MARGIN.top + ((max - value) / (max - min)) * plotHeight;
  const line = (series: number[]) =>
    series.map((value, i) => `${i ? "L" : "M"}${x(offsets[i]!).toFixed(1)} ${y(value).toFixed(1)}`).join("");
  const band = (low: number[], high: number[]) =>
    `${line(high)}${[...low]
      .reverse()
      .map((value, i) => `L${x(offsets[low.length - 1 - i]!).toFixed(1)} ${y(value).toFixed(1)}`)
      .join("")}Z`;
  const labelYs = spread(
    groups.map((group) => y(group.mean[group.mean.length - 1] ?? 0)),
    16,
  );
  const at = (offset: number) => offsets.indexOf(offset);

  function onMove(event: PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const offset = Math.round(first + ((event.clientX - bounds.left - PATH_MARGIN.left) / Math.max(1, plotWidth)) * (last - first));
    setHover(offset >= first && offset <= last ? offsets.indexOf(offset) : null);
  }

  const hoverX = hover === null ? 0 : x(offsets[hover]!);
  const tooltipStyle =
    hover === null
      ? undefined
      : hoverX > width / 2
        ? { right: width - hoverX + 12, top: PATH_MARGIN.top }
        : { left: hoverX + 12, top: PATH_MARGIN.top };

  return (
    <ChartCard
      title="Abnormal return around the release, by tone surprise"
      note="Cumulative return over the market, zero on the day before the release. Shaded: 95% confidence band. All tested releases, 2016 to 2025."
      legend={groups.map((group, index) => (
        <span key={group.label} className="legend-item">
          <ThirdSwatch index={index} /> {group.label} ({formatNumber(group.n)})
        </span>
      ))}
      table={
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Tone surprise</th>
              <th scope="col" className="col-num">Releases</th>
              <th scope="col" className="col-num">Day +1</th>
              <th scope="col" className="col-num">Day +{drift1}</th>
              <th scope="col" className="col-num">Day +{last}</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <tr key={group.label}>
                <td>{group.label}</td>
                <td className="col-num num">{formatNumber(group.n)}</td>
                <td className="col-num num">{pct(group.mean[at(1)] ?? 0, 2)}</td>
                <td className="col-num num">{pct(group.mean[at(drift1)] ?? 0, 2)}</td>
                <td className="col-num num">{pct(group.mean[at(last)] ?? 0, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <div className="chart" ref={frame}>
        {width > 0 ? (
          <svg
            height={PATH_HEIGHT}
            role="img"
            aria-label="Cumulative abnormal return from 10 days before to 60 days after the release, for the most negative, middle and most positive thirds of tone surprise. Use the table view for exact values."
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
          >
            <rect
              className="chart-band"
              x={x(drift0)}
              y={PATH_MARGIN.top}
              width={Math.max(0, x(drift1) - x(drift0))}
              height={plotHeight}
            />
            <text className="chart-tick" x={x(drift0) + 6} y={PATH_MARGIN.top - 10}>
              Drift window, days +{drift0} to +{drift1}
            </text>
            {ticks(min, max, 5).map((tick) => (
              <g key={tick}>
                <line
                  className={tick === 0 ? "chart-zero" : "chart-grid"}
                  x1={PATH_MARGIN.left}
                  x2={PATH_MARGIN.left + plotWidth}
                  y1={y(tick)}
                  y2={y(tick)}
                />
                <text className="chart-tick" x={PATH_MARGIN.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">
                  {`${formatSigned(tick * 100, 1)}%`}
                </text>
              </g>
            ))}
            {offsets
              .filter((offset) => offset % (plotWidth < 360 ? 20 : 10) === 0)
              .map((offset) => (
                <text key={offset} className="chart-tick" x={x(offset)} y={PATH_HEIGHT - 20} textAnchor="middle">
                  {offset > 0 ? `+${offset}` : offset}
                </text>
              ))}
            <text
              className="chart-tick chart-tick-muted"
              x={PATH_MARGIN.left + plotWidth}
              y={PATH_HEIGHT - 4}
              textAnchor="end"
            >
              trading days from the release
            </text>
            <line className="chart-marker" x1={x(0)} x2={x(0)} y1={PATH_MARGIN.top - 4} y2={PATH_MARGIN.top + plotHeight} />
            {groups.map((group, index) => (
              <path key={`band-${group.label}`} className="path-band" data-tone={THIRD_TONE[index]} d={band(group.low, group.high)} />
            ))}
            {groups.map((group, index) => (
              <path key={`line-${group.label}`} className="path-line" data-tone={THIRD_TONE[index]} d={line(group.mean)} />
            ))}
            {groups.map((group, index) => (
              <text key={`label-${group.label}`} className="series-label" x={x(last) + 8} y={labelYs[index]} dy="0.32em">
                {THIRD_SHORT[index]}
              </text>
            ))}
            {hover !== null ? (
              <>
                <line className="chart-cross" x1={hoverX} x2={hoverX} y1={PATH_MARGIN.top} y2={PATH_MARGIN.top + plotHeight} />
                {groups.map((group, index) => (
                  <circle
                    key={group.label}
                    className="path-dot"
                    data-tone={THIRD_TONE[index]}
                    cx={hoverX}
                    cy={y(group.mean[hover] ?? 0)}
                    r={4}
                  />
                ))}
              </>
            ) : null}
          </svg>
        ) : (
          <div style={{ height: PATH_HEIGHT }} />
        )}
        {hover !== null ? (
          <div className="tooltip" style={tooltipStyle} role="presentation">
            <div className="tooltip-value">
              Day {offsets[hover]! > 0 ? `+${offsets[hover]}` : offsets[hover]}
            </div>
            <ul className="tooltip-list">
              {groups.map((group, index) => (
                <li key={group.label}>
                  <ThirdSwatch index={index} />
                  <span>{THIRD_SHORT[index]}</span>
                  <span className="num">{pct(group.mean[hover] ?? 0, 2)}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </ChartCard>
  );
}

/* ------------------------------------------------- reaction vs drift (bars) */

const RD_HEIGHT = 260;
const RD_MARGIN = { top: 16, right: 12, bottom: 44, left: 52 };

export function ReactionDriftChart() {
  const { groups } = earnings.car_paths;
  const [r0, r1] = earnings.plan.reaction;
  const [d0, d1] = earnings.plan.drift;
  const windows: { title: string; sub: string; pick: (g: (typeof groups)[number]) => Interval }[] = [
    { title: "Reaction", sub: `days ${r0} to +${r1}`, pick: (g) => g.reaction },
    { title: "Drift", sub: `days +${d0} to +${d1}`, pick: (g) => g.drift },
  ];
  const [frame, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<string | null>(null);
  const values = windows.flatMap((w) => groups.flatMap((g) => [w.pick(g).low, w.pick(g).high]));
  const extent = Math.max(...values.map(Math.abs)) * 1.12 || 0.001;
  const min = Math.min(0, ...values) < 0 ? -extent : 0;
  const max = Math.max(0, ...values) > 0 ? extent : 0;
  const plotWidth = Math.max(0, width - RD_MARGIN.left - RD_MARGIN.right);
  const plotHeight = RD_HEIGHT - RD_MARGIN.top - RD_MARGIN.bottom;
  const y = (value: number) => RD_MARGIN.top + ((max - value) / (max - min || 1)) * plotHeight;
  const groupWidth = plotWidth / windows.length;
  const barWidth = Math.min(40, (groupWidth * 0.7) / groups.length);
  const barX = (w: number, g: number) =>
    RD_MARGIN.left + groupWidth * w + groupWidth / 2 + (g - (groups.length - 1) / 2) * (barWidth + 6);
  const hovered = hover === null ? null : hover.split(":").map(Number) as [number, number];

  return (
    <ChartCard
      title="Reaction and drift by tone surprise"
      note="Mean abnormal return in basis points, with 95% intervals. The question is whether the bars on the right differ."
      legend={groups.map((group, index) => (
        <span key={group.label} className="legend-item">
          <span className="swatch" data-tone={THIRD_TONE[index]} aria-hidden="true" /> {group.label}
        </span>
      ))}
      table={
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Tone surprise</th>
              {windows.map((w) => (
                <th key={w.title} scope="col" className="col-num">
                  {w.title} (bps) [95% CI]
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <tr key={group.label}>
                <td>{group.label}</td>
                {windows.map((w) => {
                  const v = w.pick(group);
                  return (
                    <td key={w.title} className="col-num num">
                      {bps(v.mean)} [{bps(v.low)}, {bps(v.high)}]
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <div className="chart" ref={frame}>
        {width > 0 ? (
          <svg height={RD_HEIGHT} role="img" aria-label="Mean reaction and drift abnormal returns for each third of tone surprise. Use the table view for exact values." onPointerLeave={() => setHover(null)}>
            {ticks(min, max, 4).map((tick) => (
              <g key={tick}>
                <line className={tick === 0 ? "chart-zero" : "chart-grid"} x1={RD_MARGIN.left} x2={RD_MARGIN.left + plotWidth} y1={y(tick)} y2={y(tick)} />
                <text className="chart-tick" x={RD_MARGIN.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">
                  {bps(tick)}
                </text>
              </g>
            ))}
            {windows.map((w, wi) => (
              <g key={w.title}>
                <text className="chart-tick" x={RD_MARGIN.left + groupWidth * wi + groupWidth / 2} y={RD_HEIGHT - 24} textAnchor="middle">
                  {w.title}
                </text>
                <text className="chart-tick chart-tick-muted" x={RD_MARGIN.left + groupWidth * wi + groupWidth / 2} y={RD_HEIGHT - 9} textAnchor="middle">
                  {w.sub}
                </text>
                {groups.map((group, gi) => {
                  const v = w.pick(group);
                  const cx = barX(wi, gi);
                  return (
                    <g key={group.label} onPointerEnter={() => setHover(`${wi}:${gi}`)}>
                      <rect className="chart-hit" x={cx - barWidth / 2 - 3} y={RD_MARGIN.top} width={barWidth + 6} height={plotHeight} />
                      <rect
                        className="bar"
                        data-tone={THIRD_TONE[gi]}
                        x={cx - barWidth / 2}
                        y={y(Math.max(0, v.mean))}
                        width={barWidth}
                        height={Math.max(1, Math.abs(y(v.mean) - y(0)))}
                        rx={3}
                      />
                      <line className="whisker" x1={cx} x2={cx} y1={y(v.low)} y2={y(v.high)} />
                      <line className="whisker" x1={cx - 5} x2={cx + 5} y1={y(v.low)} y2={y(v.low)} />
                      <line className="whisker" x1={cx - 5} x2={cx + 5} y1={y(v.high)} y2={y(v.high)} />
                    </g>
                  );
                })}
              </g>
            ))}
          </svg>
        ) : (
          <div style={{ height: RD_HEIGHT }} />
        )}
        {hovered ? (
          <div
            className="tooltip"
            role="presentation"
            style={{ left: Math.min(barX(hovered[0], hovered[1]) + 14, Math.max(0, width - 220)), top: RD_MARGIN.top }}
          >
            <div className="tooltip-value">
              {groups[hovered[1]]!.label}, {windows[hovered[0]]!.title.toLowerCase()}
            </div>
            <p className="tooltip-meta num">
              {bps(windows[hovered[0]]!.pick(groups[hovered[1]]!).mean, 1)} bps, 95% CI{" "}
              {bps(windows[hovered[0]]!.pick(groups[hovered[1]]!).low, 1)} to{" "}
              {bps(windows[hovered[0]]!.pick(groups[hovered[1]]!).high, 1)}
            </p>
          </div>
        ) : null}
      </div>
    </ChartCard>
  );
}

/* ------------------------------------------------------ tone by quarter */

const TI_HEIGHT = 240;
const TI_MARGIN = { top: 16, right: 16, bottom: 30, left: 48 };

export function ToneIndexChart() {
  const points = earnings.tone_index;
  const [frame, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const tones = points.map((p) => p.tone);
  const pad = (Math.max(...tones) - Math.min(...tones)) * 0.15 || 0.01;
  const min = Math.min(...tones) - pad;
  const max = Math.max(...tones) + pad;
  const plotWidth = Math.max(0, width - TI_MARGIN.left - TI_MARGIN.right);
  const plotHeight = TI_HEIGHT - TI_MARGIN.top - TI_MARGIN.bottom;
  const x = (i: number) => TI_MARGIN.left + (i / Math.max(1, points.length - 1)) * plotWidth;
  const y = (v: number) => TI_MARGIN.top + ((max - v) / (max - min)) * plotHeight;
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(p.tone).toFixed(1)}`).join("");
  const yearStep = plotWidth < 420 ? 2 : 1;

  function onMove(event: PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const i = Math.round(((event.clientX - bounds.left - TI_MARGIN.left) / Math.max(1, plotWidth)) * (points.length - 1));
    setHover(i >= 0 && i < points.length ? i : null);
  }

  return (
    <ChartCard
      title="How companies sound, quarter by quarter"
      note="Mean Loughran-McDonald net tone of every earnings release in the quarter, (positive − negative) / (positive + negative)."
      table={
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Quarter</th>
              <th scope="col" className="col-num">Mean tone</th>
              <th scope="col" className="col-num">Releases</th>
            </tr>
          </thead>
          <tbody>
            {points.map((p) => (
              <tr key={p.quarter}>
                <td className="num">{p.quarter}</td>
                <td className="col-num num">{formatSigned(p.tone, 3)}</td>
                <td className="col-num num">{formatNumber(p.n)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <div className="chart" ref={frame}>
        {width > 0 ? (
          <svg height={TI_HEIGHT} role="img" aria-label="Mean tone of earnings releases by quarter. Use the table view for exact values." onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
            {ticks(min, max, 4).map((tick) => (
              <g key={tick}>
                <line className="chart-grid" x1={TI_MARGIN.left} x2={TI_MARGIN.left + plotWidth} y1={y(tick)} y2={y(tick)} />
                <text className="chart-tick" x={TI_MARGIN.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">
                  {formatSigned(tick, 2)}
                </text>
              </g>
            ))}
            {points.map((p, i) =>
              p.quarter.endsWith("Q1") && Number(p.quarter.slice(0, 4)) % yearStep === 0 ? (
                <text key={p.quarter} className="chart-tick" x={x(i)} y={TI_HEIGHT - 8} textAnchor="middle">
                  {p.quarter.slice(0, 4)}
                </text>
              ) : null,
            )}
            <path className="series-line" data-series="sentiment" d={d} />
            {hover !== null ? (
              <>
                <line className="chart-cross" x1={x(hover)} x2={x(hover)} y1={TI_MARGIN.top} y2={TI_MARGIN.top + plotHeight} />
                <circle className="series-dot" data-series="sentiment" cx={x(hover)} cy={y(points[hover]!.tone)} r={4} />
              </>
            ) : null}
          </svg>
        ) : (
          <div style={{ height: TI_HEIGHT }} />
        )}
        {hover !== null ? (
          <div
            className="tooltip"
            role="presentation"
            style={x(hover) > width / 2 ? { right: width - x(hover) + 12, top: TI_MARGIN.top } : { left: x(hover) + 12, top: TI_MARGIN.top }}
          >
            <div className="tooltip-value">
              {points[hover]!.quarter} <span className="num">{formatSigned(points[hover]!.tone, 3)}</span>
            </div>
            <p className="tooltip-meta">{formatNumber(points[hover]!.n)} releases</p>
          </div>
        ) : null}
      </div>
    </ChartCard>
  );
}

/* --------------------------------------------- honest vs date-only timing */

const TE_HEIGHT = 300;
const TE_MARGIN = { top: 22, right: 96, bottom: 28, left: 52 };
const TIMING = [
  { key: "honest" as const, series: "sentiment" as const, label: "Honest timing", short: "Honest" },
  { key: "naive" as const, series: "lookahead" as const, label: "Date-only timing (look-ahead)", short: "Date-only" },
];

export function TimingEquityChart() {
  const { equity, primary, naive } = earnings.portfolio;
  const [frame, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = equity.dates.length;
  const growth = { honest: equity.honest.map((v) => v - 1), naive: equity.naive.map((v) => v - 1) };
  const values = [...growth.honest, ...growth.naive];
  const pad = (Math.max(...values) - Math.min(...values)) * 0.06 || 0.01;
  const min = Math.min(0, ...values) - pad;
  const max = Math.max(0, ...values) + pad;
  const plotWidth = Math.max(0, width - TE_MARGIN.left - TE_MARGIN.right);
  const plotHeight = TE_HEIGHT - TE_MARGIN.top - TE_MARGIN.bottom;
  const x = (i: number) => TE_MARGIN.left + (i / Math.max(1, n - 1)) * plotWidth;
  const y = (v: number) => TE_MARGIN.top + ((max - v) / (max - min)) * plotHeight;
  const holdoutIndex = Math.max(0, equity.dates.findIndex((d) => d >= equity.holdout_start));
  const labelYs = spread(TIMING.map((t) => y(growth[t.key][n - 1] ?? 0)), 16);
  const years = equity.dates.flatMap((d, i) => (i === 0 || d.slice(0, 4) !== equity.dates[i - 1]!.slice(0, 4) ? [i] : []));
  const yearStep = plotWidth < 480 ? 2 : 1;

  function onMove(event: PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const i = Math.round(((event.clientX - bounds.left - TE_MARGIN.left) / Math.max(1, plotWidth)) * (n - 1));
    setHover(i >= 0 && i < n ? i : null);
  }

  return (
    <ChartCard
      title="The timing trap: same signal, two clocks"
      note={`Long the most positive third of tone surprises, short the most negative, held ${earnings.plan.hold} days, net of ${earnings.plan.cost_bps} bps per dollar traded. Date-only timing trades at the close of the filing date, before after-hours news was public.`}
      legend={TIMING.map((t) => (
        <span key={t.key} className="legend-item">
          <SeriesSwatch series={t.series} /> {t.label}
        </span>
      ))}
      table={
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Timing</th>
              <th scope="col" className="col-num">Cumulative</th>
              <th scope="col" className="col-num">Net Sharpe, development</th>
              <th scope="col" className="col-num">Net Sharpe, holdout</th>
            </tr>
          </thead>
          <tbody>
            {TIMING.map((t) => {
              const periods = t.key === "honest" ? primary : naive;
              return (
                <tr key={t.key}>
                  <td>{t.label}</td>
                  <td className="col-num num">{pct(growth[t.key][n - 1] ?? 0)}</td>
                  <td className="col-num num">{formatSigned(periods.development.sharpe)}</td>
                  <td className="col-num num">{formatSigned(periods.holdout.sharpe)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      }
    >
      <div className="chart" ref={frame}>
        {width > 0 ? (
          <svg height={TE_HEIGHT} role="img" aria-label="Cumulative net return of the tone strategy with honest and with date-only timing. Use the table view for exact values." onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
            {ticks(min, max, 5).map((tick) => (
              <g key={tick}>
                <line className={tick === 0 ? "chart-zero" : "chart-grid"} x1={TE_MARGIN.left} x2={TE_MARGIN.left + plotWidth} y1={y(tick)} y2={y(tick)} />
                <text className="chart-tick" x={TE_MARGIN.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">
                  {`${Math.round(tick * 100)}%`}
                </text>
              </g>
            ))}
            {years
              .filter((i) => Number(equity.dates[i]!.slice(0, 4)) % yearStep === 0)
              .map((i) => (
                <text key={i} className="chart-tick" x={x(i)} y={TE_HEIGHT - 8} textAnchor="middle">
                  {equity.dates[i]!.slice(0, 4)}
                </text>
              ))}
            <line className="chart-marker" x1={x(holdoutIndex)} x2={x(holdoutIndex)} y1={TE_MARGIN.top - 6} y2={TE_MARGIN.top + plotHeight} />
            <text className="chart-tick" x={x(holdoutIndex) + 6} y={TE_MARGIN.top - 8}>
              Holdout
            </text>
            {[...TIMING].reverse().map((t) => (
              <path
                key={t.key}
                className="series-line"
                data-series={t.series}
                d={growth[t.key].map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join("")}
              />
            ))}
            {TIMING.map((t, index) => (
              <text key={t.key} className="series-label" x={x(n - 1) + 8} y={labelYs[index]} dy="0.32em">
                {t.short}
              </text>
            ))}
            {hover !== null ? (
              <>
                <line className="chart-cross" x1={x(hover)} x2={x(hover)} y1={TE_MARGIN.top} y2={TE_MARGIN.top + plotHeight} />
                {TIMING.map((t) => (
                  <circle key={t.key} className="series-dot" data-series={t.series} cx={x(hover)} cy={y(growth[t.key][hover] ?? 0)} r={4} />
                ))}
              </>
            ) : null}
          </svg>
        ) : (
          <div style={{ height: TE_HEIGHT }} />
        )}
        {hover !== null ? (
          <div
            className="tooltip"
            role="presentation"
            style={x(hover) > width / 2 ? { right: width - x(hover) + 12, top: TE_MARGIN.top } : { left: x(hover) + 12, top: TE_MARGIN.top }}
          >
            <div className="tooltip-value">{equity.dates[hover]}</div>
            <ul className="tooltip-list">
              {TIMING.map((t) => (
                <li key={t.key}>
                  <SeriesSwatch series={t.series} />
                  <span>{t.short}</span>
                  <span className="num">{pct(growth[t.key][hover] ?? 0)}</span>
                </li>
              ))}
            </ul>
            {hover >= holdoutIndex ? <p className="tooltip-meta">Holdout period</p> : null}
          </div>
        ) : null}
      </div>
    </ChartCard>
  );
}

/* ------------------------------------------------------- release sessions */

const SESSION_ORDER = ["pre-market", "market hours", "after close", "non-trading day"];

export function SessionBars() {
  const sessions = earnings.timing.sessions;
  const entries = SESSION_ORDER.filter((name) => name in sessions).map((name) => ({ name, count: sessions[name]! }));
  const total = entries.reduce((sum, entry) => sum + entry.count, 0) || 1;
  const max = Math.max(1, ...entries.map((entry) => entry.count));
  return (
    <ol className="funnel" aria-label="Releases by New York trading session">
      {entries.map((entry) => (
        <li key={entry.name} className="funnel-row share-row">
          <span className="funnel-name">
            <strong>{entry.name[0]!.toUpperCase() + entry.name.slice(1)}</strong>
          </span>
          <span className="funnel-track" aria-hidden="true">
            <span className="funnel-fill" style={{ width: `${(entry.count / max) * 100}%` }} />
          </span>
          <span className="funnel-value">{formatNumber(entry.count)}</span>
          <span className="funnel-drop">{Math.round((entry.count / total) * 100)}%</span>
        </li>
      ))}
    </ol>
  );
}
