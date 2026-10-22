import type { ReactNode } from "react";

import { formatSigned } from "@/lib/format";
import type { SignalPoint, Stance } from "@/lib/types";

import { CheckGlyph, CrossGlyph, RingGlyph, WarningGlyph } from "./icons";

type Tone = "good" | "warning" | "critical" | "neutral";

const TONE_GLYPH = {
  good: CheckGlyph,
  warning: WarningGlyph,
  critical: CrossGlyph,
  neutral: RingGlyph,
} as const;

/** A status is never colour alone: it always carries a glyph and a label. */
export function StatusTag({
  tone,
  children,
  strong = false,
  title,
}: {
  tone: Tone;
  children: ReactNode;
  strong?: boolean;
  title?: string;
}) {
  const Glyph = TONE_GLYPH[tone];
  return (
    <span className={`status${strong ? " status-strong" : ""}`} data-tone={tone} title={title}>
      <Glyph />
      {children}
    </span>
  );
}

export function StatTile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}</span>
      {sub ? <span className="tile-sub">{sub}</span> : null}
    </div>
  );
}

export type Sign = "pos" | "neg" | "zero";

export function signOf(value: number): Sign {
  if (value > 0) return "pos";
  if (value < 0) return "neg";
  return "zero";
}

/** Signed value on a shared zero baseline: length is magnitude, side and hue are polarity. */
export function PolarityBar({ value, abstained = false }: { value: number; abstained?: boolean }) {
  const sign = signOf(value);
  const width = `${Math.min(1, Math.abs(value)) * 50}%`;
  return (
    <span className="polarity" data-abstained={abstained}>
      <span className="polarity-track" aria-hidden="true">
        {sign !== "zero" && !abstained ? (
          <span className="polarity-fill" data-sign={sign} style={{ width }} />
        ) : null}
      </span>
      <span className="polarity-value num">{abstained ? "n/a" : formatSigned(value)}</span>
    </span>
  );
}

/** A ratio against a fixed limit of 1. The track is a lighter step of the same grey. */
export function Meter({ value }: { value: number }) {
  const clamped = Math.max(0, Math.min(1, value));
  return (
    <span className="meter">
      <span className="meter-track" aria-hidden="true">
        <span className="meter-fill" style={{ width: `${clamped * 100}%`, display: "block" }} />
      </span>
      <span className="num">{clamped.toFixed(2)}</span>
    </span>
  );
}

export function Dot({ sign, hollow = false }: { sign: Sign; hollow?: boolean }) {
  return <span className="dot" data-sign={sign} data-hollow={hollow} aria-hidden="true" />;
}

const STANCE_LABEL: Record<Stance, string> = {
  positive: "Positive lean",
  negative: "Negative lean",
  neutral: "Neutral",
};

export function stanceLabel(signal: Pick<SignalPoint, "stance" | "abstained">): string {
  return signal.abstained ? "Abstained" : STANCE_LABEL[signal.stance];
}

export function stanceSign(signal: Pick<SignalPoint, "stance" | "abstained">): Sign {
  if (signal.abstained) return "zero";
  if (signal.stance === "positive") return "pos";
  if (signal.stance === "negative") return "neg";
  return "zero";
}

export function StanceTag({ signal }: { signal: Pick<SignalPoint, "stance" | "abstained"> }) {
  return (
    <span className="stance">
      <Dot sign={stanceSign(signal)} hollow={signal.abstained} />
      {stanceLabel(signal)}
    </span>
  );
}

const SPARK_W = 128;
const SPARK_H = 34;
const SPARK_PAD = 5;

/** Step line of the normalised score; the latest value is marked with a dot. */
export function Sparkline({ points }: { points: SignalPoint[] }) {
  if (points.length === 0) {
    return <svg className="spark" width={SPARK_W} height={SPARK_H} aria-hidden="true" />;
  }
  const first = Date.parse(points[0]!.as_of);
  const last = Date.parse(points[points.length - 1]!.as_of);
  const span = Math.max(1, last - first);
  const x = (iso: string) =>
    SPARK_PAD + ((Date.parse(iso) - first) / span) * (SPARK_W - 2 * SPARK_PAD - 4);
  const y = (value: number) => SPARK_PAD + ((1 - value) / 2) * (SPARK_H - 2 * SPARK_PAD);

  let path = "";
  points.forEach((point, index) => {
    const px = x(point.as_of).toFixed(1);
    const py = y(point.score_normalized).toFixed(1);
    path += index === 0 ? `M${px} ${py}` : `H${px}V${py}`;
  });
  const end = points[points.length - 1]!;
  return (
    <svg className="spark" width={SPARK_W} height={SPARK_H} aria-hidden="true" focusable="false">
      <path className="spark-zero" d={`M0 ${y(0)}H${SPARK_W}`} />
      <path className="spark-line" d={path} />
      <circle
        className="chart-dot"
        data-sign={stanceSign(end)}
        data-hollow={end.abstained}
        cx={x(end.as_of)}
        cy={y(end.score_normalized)}
        r={4}
      />
    </svg>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="card empty" role="alert">
      <p>
        <StatusTag tone="critical">Could not load data</StatusTag>
      </p>
      <p style={{ marginTop: 8 }}>{message}</p>
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="card empty" aria-busy="true">
      <span className="muted">{label}…</span>
    </div>
  );
}
