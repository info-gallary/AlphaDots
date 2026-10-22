/** Shape of `src/data/snapshot.json`, written by `scripts/export_snapshot.py`. */

import type { DeadLetter, EventItem, FeedMode, MonitorSummary, ResearchReport, Signal } from "../lib/types";

export interface SnapshotListing {
  symbol: string;
  valid_from: string; // YYYY-MM-DD
  valid_to: string | null; // inclusive; null means still listed
}

export interface SnapshotInstrument {
  instrument_id: string;
  name: string;
  sector: string;
  aliases: string[];
  listings: SnapshotListing[];
}

export interface Snapshot {
  snapshot_version: number;
  description: string;
  generated_by: string;
  feed_mode: FeedMode;
  fixtures: Record<string, string>;
  models: Record<string, string>;
  reference: { version: string; license: string; instruments: SnapshotInstrument[] };
  /** Oldest first. Each carries its sentiment score, which has its own availability time. */
  events: EventItem[];
  /** Signal payloads exactly as published on `signals.generated.v1`, oldest first. */
  signals: Signal[];
  dead_letters: DeadLetter[];
  /**
   * One research brief per issuer, keyed by the symbol it traded as at export time: real
   * runs of the research graph (mock model) against this snapshot. `latency_ms` is null
   * because a timing would make the file differ on every export.
   */
  research?: Record<string, ResearchReport>;
  /** One run of the monitor graph at export time; `latency_ms` null for the same reason. */
  monitor?: MonitorSummary;
}
