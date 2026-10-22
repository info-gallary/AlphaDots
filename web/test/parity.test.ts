/**
 * Parity: the snapshot read model must answer exactly like the real FastAPI service.
 *
 * `parity.golden.json` holds responses recorded from the Python API by
 * `scripts/export_snapshot.py`, over the same data as `src/data/snapshot.json`. Each case
 * is replayed here against the TypeScript implementation.
 *
 *   pnpm test
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { handleSnapshotRequest } from "../src/server/snapshot-api.ts";
import type { Snapshot } from "../src/server/snapshot-types.ts";

interface GoldenCase {
  path: string;
  status: number;
  body: Record<string, unknown>;
}

const read = <T>(relative: string): T =>
  JSON.parse(readFileSync(new URL(relative, import.meta.url), "utf-8")) as T;

const snapshot = read<Snapshot>("../src/data/snapshot.json");
const { cases } = read<{ cases: GoldenCase[] }>("./parity.golden.json");

/** Drop what legitimately differs per request, exactly as the exporter did. */
function comparable(body: Record<string, unknown>): Record<string, unknown> {
  const copy = structuredClone(body);
  const meta = copy.meta as Record<string, unknown> | undefined;
  if (meta) delete meta.generated_at;
  const error = copy.error as Record<string, unknown> | undefined;
  if (error) return { error: { code: error.code, message: error.message } };
  return copy;
}

function request(path: string) {
  const url = new URL(path, "http://parity");
  const segments = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  return handleSnapshotRequest(snapshot, segments, url.searchParams);
}

test("golden file covers the main routes and error paths", () => {
  assert.ok(cases.length >= 30, `only ${cases.length} cases recorded`);
  const statuses = new Set(cases.map((item) => item.status));
  for (const status of [200, 400, 404, 422]) assert.ok(statuses.has(status), `no ${status} case`);
});

for (const golden of cases) {
  test(`parity ${golden.status} ${golden.path}`, () => {
    const result = request(golden.path);
    assert.equal(result.status, golden.status);
    assert.deepStrictEqual(comparable(result.body as Record<string, unknown>), golden.body);
  });
}

test("point-in-time: nothing in any response is newer than as_of", () => {
  const asOf = "2026-03-06T12:00:00Z";
  const bound = Date.parse(asOf);
  const events = request(`/v1/events?limit=200&as_of=${asOf}`).body as {
    data: { available_at: string; sentiment: { available_at: string } | null }[];
  };
  assert.ok(events.data.length > 0);
  for (const event of events.data) {
    assert.ok(Date.parse(event.available_at) <= bound);
    if (event.sentiment) assert.ok(Date.parse(event.sentiment.available_at) <= bound);
  }
  const signals = request(`/v1/signals/CBHB?as_of=${asOf}`).body as {
    latest: { available_at: string; contributions: { available_at: string }[] } | null;
  };
  assert.ok(signals.latest);
  assert.ok(Date.parse(signals.latest.available_at) <= bound);
  for (const item of signals.latest.contributions) assert.ok(Date.parse(item.available_at) <= bound);
});

test("pagination walks every event exactly once", () => {
  const seen: string[] = [];
  let cursor: string | null = null;
  do {
    const query: string = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
    const page = request(`/v1/events?limit=7${query}`).body as {
      data: { event_id: string }[];
      page: { next_cursor: string | null };
    };
    seen.push(...page.data.map((event) => event.event_id));
    cursor = page.page.next_cursor;
  } while (cursor);
  assert.equal(seen.length, snapshot.events.length);
  assert.equal(new Set(seen).size, snapshot.events.length);
});
