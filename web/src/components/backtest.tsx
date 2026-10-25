"use client";

import { useState, type PointerEvent } from "react";

import { formatSigned } from "@/lib/format";
import { SHORT_LABEL, STRATEGY_ORDER, type StrategyKey, type World } from "@/lib/research";
import { useWidth } from "@/lib/useWidth";

import { ChartCard } from "./charts";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function pct(value: number, digits = 1): string {
  return `${formatSigned(value * 100, digits)}%`;
}

/** A tick step of 1, 2, 2.5 or 5 x 10^k giving roughly `count` intervals over `span`. */
function niceStep(span: number, count: number): number {
  const raw = span / Math.max(1, count);
  const power = 10 ** Math.floor(Math.log10(raw));
  const unit = [1, 2, 2.5, 5, 10].find((candidate) => candidate * power >= raw) ?? 10;
  return unit * power;
}

export function ticks(min: number, max: number, count: number): number[] {
  const step = niceStep(max - min, count);
  const out: number[] = [];
  for (let tick = Math.ceil(min / step) * step; tick <= max + step / 1e6; tick += step) {
    out.push(Math.abs(tick) < step / 1e6 ? 0 : tick);
  }
  return out;
}

export function SeriesSwatch({ series }: { series: StrategyKey }) {
  return <span className="swatch swatch-line" data-series={series} aria-hidden="true" />;
}

/* ------------------------------------------------------------- equity curves */

const EQ_HEIGHT = 320;
const EQ_MARGIN = { top: 22, right: 112, bottom: 28, left: 52 };
const LABELLED: StrategyKey[] = ["sentiment", "lookahead"];

export function EquityChart({ world }: { world: World }) {
  const [frame, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const [hidden, setHidden] = useState<ReadonlySet<StrategyKey>>(new Set());
  const shown = STRATEGY_ORDER.filter((key) => !hidden.has(key));
  const toggle = (key: StrategyKey) =>
    setHidden((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else if (shown.length > 1) next.add(key);
      return next;
    });
  const n = world.dates.length;
  const values = shown.flatMap((key) => world.strategies[key].equity);
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  const pad = (high - low) * 0.06 || 0.01;
  const min = low - pad;
  const max = high + pad;
  const plotWidth = Math.max(0, width - EQ_MARGIN.left - EQ_MARGIN.right);
  const plotHeight = EQ_HEIGHT - EQ_MARGIN.top - EQ_MARGIN.bottom;
  const x = (index: number) => EQ_MARGIN.left + (index / Math.max(1, n - 1)) * plotWidth;
  const y = (value: number) => EQ_MARGIN.top + ((max - value) / (max - min)) * plotHeight;

  const monthStarts = world.dates.flatMap((date, index) =>
    index === 0 || date.slice(0, 7) !== world.dates[index - 1]!.slice(0, 7) ? [index] : [],
  );
  const every = Math.max(1, Math.ceil(monthStarts.length / Math.max(2, Math.floor(plotWidth / 70))));
  const xTicks = monthStarts.filter((_, position) => position % every === 0);

  // Direct labels for the two series the study is about, nudged apart if they collide.
  const ends = LABELLED.filter((key) => !hidden.has(key)).map((key) => ({ key, y: y(world.strategies[key].equity[n - 1] ?? 0) })).sort(
    (a, b) => a.y - b.y,
  );
  if (ends.length === 2 && ends[1]!.y - ends[0]!.y < 16) ends[1]!.y = ends[0]!.y + 16;

  function onMove(event: PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const index = Math.round(((event.clientX - bounds.left - EQ_MARGIN.left) / Math.max(1, plotWidth)) * (n - 1));
    setHover(index >= 0 && index < n ? index : null);
  }

  const tooltipStyle =
    hover === null
      ? undefined
      : x(hover) > width / 2
        ? { right: width - x(hover) + 12, top: EQ_MARGIN.top }
        : { left: x(hover) + 12, top: EQ_MARGIN.top };

  return (
    <ChartCard
      title="Cumulative net return"
      note="Walk-forward test blocks, then the untouched holdout. After trading costs, compounded daily. Select a strategy to hide it and rescale."
      legend={STRATEGY_ORDER.map((key) => (
        <button
          key={key}
          type="button"
          className="legend-item legend-toggle"
          aria-pressed={!hidden.has(key)}
          onClick={() => toggle(key)}
        >
          <SeriesSwatch series={key} /> {world.strategies[key].label}
        </button>
      ))}
      table={
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Strategy</th>
              <th scope="col" className="col-num">Cumulative</th>
              <th scope="col" className="col-num">Net Sharpe (walk-forward)</th>
              <th scope="col" className="col-num">Net Sharpe (holdout)</th>
            </tr>
          </thead>
          <tbody>
            {STRATEGY_ORDER.map((key) => {
              const strategy = world.strategies[key];
              return (
                <tr key={key}>
                  <td>{strategy.label}</td>
                  <td className="col-num num">{pct(strategy.equity[n - 1] ?? 0)}</td>
                  <td className="col-num num">{formatSigned(strategy.oos.sharpe)}</td>
                  <td className="col-num num">{formatSigned(strategy.holdout.sharpe)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      }
    >
      <div className="chart" ref={frame}>
        {width > 0 ? (
          <svg
            height={EQ_HEIGHT}
            role="img"
            aria-label="Cumulative net return of five strategies. Use the table view for exact values."
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
          >
            {ticks(min, max, 5).map((tick) => (
              <g key={tick}>
                <line
                  className={tick === 0 ? "chart-zero" : "chart-grid"}
                  x1={EQ_MARGIN.left}
                  x2={EQ_MARGIN.left + plotWidth}
                  y1={y(tick)}
                  y2={y(tick)}
                />
                <text className="chart-tick" x={EQ_MARGIN.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">
                  {`${Math.round(tick * 100)}%`}
                </text>
              </g>
            ))}
            {xTicks.map((index) => {
              const date = world.dates[index]!;
              return (
                <text key={index} className="chart-tick" x={x(index)} y={EQ_HEIGHT - 8} textAnchor="middle">
                  {`${MONTHS[Number(date.slice(5, 7)) - 1]} ${date.slice(2, 4)}`}
                </text>
              );
            })}
            <line
              className="chart-marker"
              x1={x(world.holdout_start_index)}
              x2={x(world.holdout_start_index)}
              y1={EQ_MARGIN.top - 6}
              y2={EQ_MARGIN.top + plotHeight}
            />
            <text className="chart-tick" x={x(world.holdout_start_index) + 6} y={EQ_MARGIN.top - 8}>
              Holdout
            </text>
            {[...shown].reverse().map((key) => {
              const series = world.strategies[key].equity;
              const d = series.map((value, index) => `${index ? "L" : "M"}${x(index).toFixed(1)} ${y(value).toFixed(1)}`).join("");
              return <path key={key} className="series-line" data-series={key} d={d} />;
            })}
            {ends.map(({ key, y: labelY }) => (
              <text key={key} className="series-label" x={x(n - 1) + 8} y={labelY} dy="0.32em">
                {SHORT_LABEL[key]}
              </text>
            ))}
            {hover !== null ? (
              <>
                <line
                  className="chart-cross"
                  x1={x(hover)}
                  x2={x(hover)}
                  y1={EQ_MARGIN.top}
                  y2={EQ_MARGIN.top + plotHeight}
                />
                {shown.map((key) => (
                  <circle
                    key={key}
                    className="series-dot"
                    data-series={key}
                    cx={x(hover)}
                    cy={y(world.strategies[key].equity[hover] ?? 0)}
                    r={4}
                  />
                ))}
              </>
            ) : null}
          </svg>
        ) : (
          <div style={{ height: EQ_HEIGHT }} />
        )}
        {hover !== null ? (
          <div className="tooltip" style={tooltipStyle} role="presentation">
            <div className="tooltip-value">{world.dates[hover]}</div>
            <ul className="tooltip-list">
              {[...shown]
                .sort((a, b) => (world.strategies[b].equity[hover] ?? 0) - (world.strategies[a].equity[hover] ?? 0))
                .map((key) => (
                  <li key={key}>
                    <SeriesSwatch series={key} />
                    <span>{SHORT_LABEL[key]}</span>
                    <span className="num">{pct(world.strategies[key].equity[hover] ?? 0)}</span>
                  </li>
                ))}
            </ul>
            {hover >= world.holdout_start_index ? <p className="tooltip-meta">Holdout period</p> : null}
          </div>
        ) : null}
      </div>
    </ChartCard>
  );
}

/* ---------------------------------------------------------- bar-chart frame */

interface Bar {
  label: string;
  sublabel?: string;
  value: number;
  marker?: number | null;
  tone: "series" | "pos" | "neg" | "outline";
}

const BAR_HEIGHT = 240;
const BAR_MARGIN = { top: 14, right: 12, bottom: 40, left: 52 };

function BarChart({
  bars,
  format,
  label,
}: {
  bars: Bar[];
  format: (value: number) => string;
  label: string;
}) {
  const [frame, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const values = bars.flatMap((bar) => [bar.value, bar.marker ?? 0]);
  const extent = Math.max(...values.map(Math.abs)) * 1.15 || 1;
  const min = Math.min(0, ...values) < 0 ? -extent : 0;
  const max = Math.max(0, ...values) > 0 ? extent : 0;
  const plotWidth = Math.max(0, width - BAR_MARGIN.left - BAR_MARGIN.right);
  const plotHeight = BAR_HEIGHT - BAR_MARGIN.top - BAR_MARGIN.bottom;
  const y = (value: number) => BAR_MARGIN.top + ((max - value) / (max - min || 1)) * plotHeight;
  const band = plotWidth / bars.length;
  const barWidth = Math.min(44, band * 0.56);
  const centre = (index: number) => BAR_MARGIN.left + band * index + band / 2;

  return (
    <div className="chart" ref={frame}>
      {width > 0 ? (
        <svg height={BAR_HEIGHT} role="img" aria-label={`${label}. Use the table view for exact values.`} onPointerLeave={() => setHover(null)}>
          {ticks(min, max, 4).map((tick) => (
            <g key={tick}>
              <line
                className={tick === 0 ? "chart-zero" : "chart-grid"}
                x1={BAR_MARGIN.left}
                x2={BAR_MARGIN.left + plotWidth}
                y1={y(tick)}
                y2={y(tick)}
              />
              <text className="chart-tick" x={BAR_MARGIN.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">
                {format(tick)}
              </text>
            </g>
          ))}
          {bars.map((bar, index) => {
            const top = y(Math.max(0, bar.value));
            const height = Math.max(1, Math.abs(y(bar.value) - y(0)));
            return (
              <g key={bar.label} onPointerEnter={() => setHover(index)}>
                <rect
                  className="chart-hit"
                  x={BAR_MARGIN.left + band * index}
                  y={BAR_MARGIN.top}
                  width={band}
                  height={plotHeight}
                />
                <rect
                  className="bar"
                  data-tone={bar.tone}
                  x={centre(index) - barWidth / 2}
                  y={top}
                  width={barWidth}
                  height={height}
                  rx={3}
                />
                {bar.marker !== undefined && bar.marker !== null ? (
                  <circle className="bar-marker" cx={centre(index)} cy={y(bar.marker)} r={4.5} />
                ) : null}
                <text className="chart-tick" x={centre(index)} y={BAR_HEIGHT - 22} textAnchor="middle">
                  {bar.label}
                </text>
                {bar.sublabel ? (
                  <text className="chart-tick chart-tick-muted" x={centre(index)} y={BAR_HEIGHT - 8} textAnchor="middle">
                    {bar.sublabel}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
      ) : (
        <div style={{ height: BAR_HEIGHT }} />
      )}
      {hover !== null && bars[hover] ? (
        <div
          className="tooltip"
          role="presentation"
          style={{ left: Math.min(centre(hover) + 10, Math.max(0, width - 200)), top: BAR_MARGIN.top }}
        >
          <div className="tooltip-value">
            {bars[hover].label} <span className="num">{format(bars[hover].value)}</span>
          </div>
          {bars[hover].marker !== undefined && bars[hover].marker !== null ? (
            <p className="tooltip-meta">Training IC {format(bars[hover].marker!)}</p>
          ) : null}
          {bars[hover].sublabel ? <p className="tooltip-meta">{bars[hover].sublabel}</p> : null}
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------- walk-forward folds */

export function FoldChart({ world }: { world: World }) {
  const holdout = world.strategies.sentiment.holdout;
  const bars: Bar[] = [
    ...world.folds.map((fold) => ({
      label: `Fold ${fold.index + 1}`,
      sublabel: `τ ${fold.tau_days}d`,
      value: fold.test_ic ?? 0,
      marker: fold.train_ic,
      tone: "series" as const,
    })),
    { label: "Holdout", sublabel: `τ ${world.holdout_tau_days}d`, value: holdout.mean_ic ?? 0, tone: "outline" },
  ];
  return (
    <ChartCard
      title="Walk-forward: out-of-sample IC by fold"
      note="Bars: mean daily rank IC on each test block. Dots: the IC that chose τ, on training data."
      legend={
        <>
          <span className="legend-item">
            <span className="swatch" data-tone="series" aria-hidden="true" /> Test IC
          </span>
          <span className="legend-item">
            <span className="legend-dot" aria-hidden="true" /> Training IC
          </span>
          <span className="legend-item">
            <span className="swatch" data-tone="outline" aria-hidden="true" /> Untouched holdout
          </span>
        </>
      }
      table={
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Fold</th>
              <th scope="col">Test window</th>
              <th scope="col" className="col-num">τ (days)</th>
              <th scope="col" className="col-num">Train IC</th>
              <th scope="col" className="col-num">Test IC</th>
            </tr>
          </thead>
          <tbody>
            {world.folds.map((fold) => (
              <tr key={fold.index}>
                <td>{fold.index + 1}</td>
                <td className="num">
                  {fold.test_start} to {fold.test_end}
                </td>
                <td className="col-num num">{fold.tau_days}</td>
                <td className="col-num num">{formatSigned(fold.train_ic ?? 0, 3)}</td>
                <td className="col-num num">{formatSigned(fold.test_ic ?? 0, 3)}</td>
              </tr>
            ))}
            <tr>
              <td>Holdout</td>
              <td className="num">{world.dates[world.holdout_start_index]} on</td>
              <td className="col-num num">{world.holdout_tau_days}</td>
              <td className="col-num num">—</td>
              <td className="col-num num">{formatSigned(holdout.mean_ic ?? 0, 3)}</td>
            </tr>
          </tbody>
        </table>
      }
    >
      <BarChart bars={bars} format={(value) => formatSigned(value, 2)} label="Out-of-sample IC by fold" />
    </ChartCard>
  );
}

/* --------------------------------------------------------------- quintiles */

export function QuintileChart({ world }: { world: World }) {
  const bars: Bar[] = world.quintiles_bps.map((value, index) => ({
    label: `Q${index + 1}`,
    sublabel: index === 0 ? "lowest" : index === 4 ? "highest" : undefined,
    value,
    tone: value >= 0 ? "pos" : "neg",
  }));
  return (
    <ChartCard
      title="Next-day return by signal quintile"
      note="Names with recent news, sorted by signal each day; mean next-day return in basis points."
      table={
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Quintile</th>
              <th scope="col" className="col-num">Mean next-day return (bps)</th>
            </tr>
          </thead>
          <tbody>
            {world.quintiles_bps.map((value, index) => (
              <tr key={index}>
                <td>Q{index + 1}</td>
                <td className="col-num num">{formatSigned(value, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <BarChart bars={bars} format={(value) => formatSigned(value, 0)} label="Mean next-day return by quintile" />
    </ChartCard>
  );
}

/* ------------------------------------------------------------------- costs */

const COST_HEIGHT = 240;
const COST_MARGIN = { top: 16, right: 20, bottom: 34, left: 44 };

export function CostChart({ world, current }: { world: World; current: number }) {
  const [frame, width] = useWidth<HTMLDivElement>();
  const points = world.cost_sensitivity;
  const sharpes = points.map((point) => point.sharpe);
  const lo = Math.min(0, ...sharpes);
  const hi = Math.max(0, ...sharpes);
  const pad = (hi - lo) * 0.1 || 1;
  const min = lo - pad;
  const max = hi + pad;
  const maxBps = Math.max(...points.map((point) => point.bps));
  const plotWidth = Math.max(0, width - COST_MARGIN.left - COST_MARGIN.right);
  const plotHeight = COST_HEIGHT - COST_MARGIN.top - COST_MARGIN.bottom;
  const x = (bps: number) => COST_MARGIN.left + (bps / maxBps) * plotWidth;
  const y = (value: number) => COST_MARGIN.top + ((max - value) / (max - min)) * plotHeight;
  const d = points.map((point, index) => `${index ? "L" : "M"}${x(point.bps).toFixed(1)} ${y(point.sharpe).toFixed(1)}`).join("");
  const breakeven = points.findIndex((point, index) => index > 0 && point.sharpe < 0 && points[index - 1]!.sharpe >= 0);

  return (
    <ChartCard
      title="Net Sharpe vs trading cost"
      note={`News signal, walk-forward. The study charges ${current} bps per dollar traded.`}
      table={
        <table className="table">
          <thead>
            <tr>
              <th scope="col" className="col-num">Cost (bps)</th>
              <th scope="col" className="col-num">Net Sharpe</th>
            </tr>
          </thead>
          <tbody>
            {points.map((point) => (
              <tr key={point.bps}>
                <td className="col-num num">{point.bps}</td>
                <td className="col-num num">{formatSigned(point.sharpe)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <div className="chart" ref={frame}>
        {width > 0 ? (
          <svg height={COST_HEIGHT} role="img" aria-label="Net Sharpe ratio at each trading cost. Use the table view for exact values.">
            {ticks(min, max, 4).map((tick) => (
              <g key={tick}>
                <line
                  className={tick === 0 ? "chart-zero" : "chart-grid"}
                  x1={COST_MARGIN.left}
                  x2={COST_MARGIN.left + plotWidth}
                  y1={y(tick)}
                  y2={y(tick)}
                />
                <text className="chart-tick" x={COST_MARGIN.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">
                  {formatSigned(tick, 0)}
                </text>
              </g>
            ))}
            {points.map((point) => (
              <text key={point.bps} className="chart-tick" x={x(point.bps)} y={COST_HEIGHT - 16} textAnchor="middle">
                {point.bps}
              </text>
            ))}
            <text className="chart-tick chart-tick-muted" x={COST_MARGIN.left + plotWidth} y={COST_HEIGHT - 2} textAnchor="end">
              bps per dollar traded
            </text>
            <path className="series-line" data-series="sentiment" d={d} />
            {points.map((point) => (
              <circle
                key={point.bps}
                className="series-dot"
                data-series="sentiment"
                data-current={point.bps === current}
                cx={x(point.bps)}
                cy={y(point.sharpe)}
                r={point.bps === current ? 6 : 4}
              />
            ))}
            {points.map((point) =>
              point.bps === current ? (
                <text key="current" className="series-label" x={x(point.bps) + 10} y={y(point.sharpe) - 10}>
                  {formatSigned(point.sharpe)} at {point.bps} bps
                </text>
              ) : null,
            )}
          </svg>
        ) : (
          <div style={{ height: COST_HEIGHT }} />
        )}
        {breakeven > 0 ? (
          <p className="card-note">
            The edge disappears between {points[breakeven - 1]!.bps} and {points[breakeven]!.bps} bps.
          </p>
        ) : null}
      </div>
    </ChartCard>
  );
}
