import type { SVGProps } from "react";

type GlyphProps = SVGProps<SVGSVGElement>;

function Glyph({ children, ...props }: GlyphProps) {
  return (
    <svg viewBox="0 0 16 16" fill="none" aria-hidden="true" focusable="false" {...props}>
      {children}
    </svg>
  );
}

/** A 3x3 dot grid with one dot lifted out of the lattice: the "alpha" dot. */
export function BrandMark({ size = 22 }: { size?: number }) {
  const cells = [3, 11, 19];
  return (
    <svg width={size} height={size} viewBox="0 0 22 22" aria-hidden="true" focusable="false">
      {cells.flatMap((cy, row) =>
        cells.map((cx, column) =>
          row === 0 && column === 2 ? (
            <circle key={`${row}-${column}`} cx={cx} cy={cy} r={3} fill="var(--accent)" />
          ) : (
            <circle key={`${row}-${column}`} cx={cx} cy={cy} r={1.7} fill="var(--ink-3)" />
          ),
        ),
      )}
    </svg>
  );
}

export function TapeGlyph(props: GlyphProps) {
  return (
    <Glyph {...props}>
      {[3.5, 8, 12.5].map((y) => (
        <g key={y}>
          <circle cx={2.5} cy={y} r={1.4} fill="currentColor" />
          <path d={`M6 ${y}H14.5`} stroke="currentColor" strokeWidth={1.4} strokeLinecap="round" />
        </g>
      ))}
    </Glyph>
  );
}

export function IssuersGlyph(props: GlyphProps) {
  return (
    <Glyph {...props}>
      {[4, 12].flatMap((cy) =>
        [4, 12].map((cx) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r={2.4} fill="currentColor" />),
      )}
    </Glyph>
  );
}

export function SystemGlyph(props: GlyphProps) {
  return (
    <Glyph {...props}>
      <path d="M3.5 8H12.5" stroke="currentColor" strokeWidth={1.4} />
      {[3, 8, 13].map((cx) => (
        <circle key={cx} cx={cx} cy={8} r={2.1} fill="currentColor" />
      ))}
    </Glyph>
  );
}

export function OverviewGlyph(props: GlyphProps) {
  return (
    <Glyph {...props}>
      {[
        [2.5, 9, 4.5],
        [6.75, 5, 8.5],
        [11, 7, 6.5],
      ].map(([x, y, h]) => (
        <rect key={x} x={x} y={y} width={2.6} height={h} rx={1} fill="currentColor" />
      ))}
    </Glyph>
  );
}

export function ResearchGlyph(props: GlyphProps) {
  return (
    <Glyph {...props}>
      <circle cx={7} cy={7} r={4.2} stroke="currentColor" strokeWidth={1.5} />
      <path d="m10.2 10.2 3.6 3.6" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" />
      <circle cx={7} cy={7} r={1.4} fill="currentColor" />
    </Glyph>
  );
}

export function WarningGlyph(props: GlyphProps) {
  return (
    <Glyph className="status-glyph" {...props}>
      <path d="M8 1.6 15 14H1L8 1.6Z" fill="currentColor" />
      <path d="M8 6.2v3.6M8 11.6v.4" stroke="var(--surface)" strokeWidth={1.5} strokeLinecap="round" />
    </Glyph>
  );
}

export function CheckGlyph(props: GlyphProps) {
  return (
    <Glyph className="status-glyph" {...props}>
      <circle cx={8} cy={8} r={7} fill="currentColor" />
      <path d="m4.8 8.2 2.2 2.2 4.2-4.6" stroke="var(--surface)" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" />
    </Glyph>
  );
}

export function CrossGlyph(props: GlyphProps) {
  return (
    <Glyph className="status-glyph" {...props}>
      <circle cx={8} cy={8} r={7} fill="currentColor" />
      <path d="m5.4 5.4 5.2 5.2m0-5.2-5.2 5.2" stroke="var(--surface)" strokeWidth={1.6} strokeLinecap="round" />
    </Glyph>
  );
}

export function RingGlyph(props: GlyphProps) {
  return (
    <Glyph className="status-glyph" {...props}>
      <circle cx={8} cy={8} r={5.5} stroke="currentColor" strokeWidth={2} />
    </Glyph>
  );
}

export function CloseGlyph(props: GlyphProps) {
  return (
    <Glyph width={14} height={14} {...props}>
      <path d="m3 3 10 10m0-10L3 13" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" />
    </Glyph>
  );
}

export function ArrowGlyph(props: GlyphProps) {
  return (
    <Glyph {...props}>
      <path d="M3 8h9m-3.5-3.5L12 8l-3.5 3.5" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </Glyph>
  );
}
