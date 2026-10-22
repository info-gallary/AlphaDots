/** Shape of `src/data/snapshot.json`, written by `scripts/export_snapshot.py`. */

import type { DeadLetter, EventItem, FeedMode, Signal } from "../lib/types";

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
}
