"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { formatMinute, formatTime } from "@/lib/format";
import { useTheme, type Theme } from "@/lib/useTheme";

import { useAppState } from "./AppState";
import { BrandMark, IssuersGlyph, SystemGlyph, TapeGlyph } from "./icons";
import { StatusTag } from "./primitives";

const NAV = [
  { href: "/", label: "Event tape", Glyph: TapeGlyph },
  { href: "/issuers", label: "Issuers", Glyph: IssuersGlyph },
  { href: "/system", label: "Pipeline", Glyph: SystemGlyph },
] as const;

// Views named in the specification that do not exist yet. Listed, not faked.
const PLANNED = [
  { label: "Research notebook", milestone: "M4" },
  { label: "Validation workbench", milestone: "M3" },
] as const;

const MINUTE_MS = 60_000;

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

function SourceBadge() {
  const { source, feed, apiError } = useAppState();
  if (apiError) {
    return (
      <StatusTag tone="critical" strong title={apiError.message}>
        DATA UNAVAILABLE
      </StatusTag>
    );
  }
  if (source?.source === "snapshot") {
    return (
      <StatusTag
        tone="warning"
        strong
        title="Synthetic fixtures replayed through the real pipeline and frozen into this deployment. No live pipeline is attached."
      >
        SIMULATED · SNAPSHOT
      </StatusTag>
    );
  }
  if (feed?.mode === "live") return <StatusTag tone="good" strong>LIVE FEED</StatusTag>;
  if (feed?.mode === "delayed") return <StatusTag tone="warning" strong>DELAYED FEED</StatusTag>;
  return (
    <StatusTag tone="warning" strong title="Synthetic fixture replay. Not market data.">
      SIMULATED FEED
    </StatusTag>
  );
}

function AsOfControl() {
  const { asOf, setAsOf, feed } = useAppState();
  const earliest = feed?.earliest_available_at ? Date.parse(feed.earliest_available_at) : null;
  const latest = feed?.latest_available_at ? Date.parse(feed.latest_available_at) : null;
  if (earliest === null || latest === null || latest <= earliest) return null;

  const min = Math.floor(earliest / MINUTE_MS);
  const max = Math.ceil(latest / MINUTE_MS);
  const value = asOf === null ? max : Math.round(Date.parse(asOf) / MINUTE_MS);

  return (
    <div className="asof">
      <label htmlFor="asof-range">As of</label>
      <input
        id="asof-range"
        type="range"
        min={min}
        max={max}
        step={1}
        value={Math.min(max, Math.max(min, value))}
        aria-valuetext={asOf === null ? "Latest" : `${formatMinute(asOf)} UTC`}
        onChange={(event) => {
          const minute = Number(event.target.value);
          setAsOf(minute >= max ? null : new Date(minute * MINUTE_MS).toISOString());
        }}
      />
      <span className="asof-readout mono num">
        {asOf === null ? "Latest" : `${formatMinute(asOf)} UTC`}
      </span>
      {asOf !== null ? (
        <button type="button" className="btn btn-ghost" onClick={() => setAsOf(null)}>
          Reset
        </button>
      ) : null}
    </div>
  );
}

const THEMES: { value: Theme; label: string }[] = [
  { value: "system", label: "Auto" },
  { value: "dark", label: "Dark" },
  { value: "light", label: "Light" },
];

function ThemeSwitch() {
  const [theme, setTheme] = useTheme();
  return (
    <div className="segmented" role="group" aria-label="Colour theme">
      {THEMES.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={theme === option.value}
          onClick={() => setTheme(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { asOf, setAsOf, feed } = useAppState();

  return (
    <div className="app">
      <aside className="rail">
        <Link href="/" className="brand" aria-label="AlphaDots home">
          <BrandMark />
          <span className="brand-name">
            AlphaDots <span>research</span>
          </span>
        </Link>
        <nav className="nav" aria-label="Main">
          <span className="nav-label">Views</span>
          {NAV.map(({ href, label, Glyph }) => (
            <Link
              key={href}
              href={href}
              className="nav-link"
              aria-current={isActive(pathname, href) ? "page" : undefined}
            >
              <Glyph className="nav-glyph" />
              {label}
            </Link>
          ))}
        </nav>
        <div className="nav nav-planned-group">
          <span className="nav-label">Not built yet</span>
          {PLANNED.map((item) => (
            <span key={item.label} className="nav-planned">
              {item.label}
              <span className="chip">{item.milestone}</span>
            </span>
          ))}
        </div>
        <div className="rail-foot">
          <ThemeSwitch />
          <span className="rail-version">Milestone M0 · research only</span>
        </div>
      </aside>

      <div className="main">
        <header className="topbar" data-pinned={asOf !== null}>
          <div className="feed">
            <SourceBadge />
            {feed?.latest_available_at ? (
              <span className="feed-clock" title="Newest event time held by the read model">
                <span className="muted">Feed time</span>
                <span className="mono num">{formatTime(feed.latest_available_at)}</span>
                <span className="muted">UTC</span>
              </span>
            ) : null}
          </div>
          <AsOfControl />
          <span className="topbar-note">Research only · not investment advice</span>
        </header>
        <main className="content" id="main">
          {asOf !== null ? (
            <div className="pinned-banner" role="status">
              <span>
                Pinned to <strong className="mono num">{formatTime(asOf)} UTC</strong>. Everything
                below is limited to what the system could have known at that moment.
              </span>
              <button type="button" className="btn" onClick={() => setAsOf(null)}>
                Return to latest
              </button>
            </div>
          ) : null}
          {children}
        </main>
      </div>
    </div>
  );
}
