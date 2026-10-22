import { readFile } from "node:fs/promises";
import path from "node:path";

import type { Snapshot } from "./snapshot-types";

/**
 * Where the dashboard's data comes from.
 *
 * - `live`: `ALPHADOTS_API_URL` points at a running AlphaDots API, and requests are proxied.
 * - `snapshot`: no API configured, so requests are answered from the bundled read-model
 *   snapshot. This is what a fresh Vercel deployment uses: Vercel cannot host the
 *   streaming pipeline, but it can host this.
 */
export type DataSource = { kind: "live"; upstream: URL } | { kind: "snapshot" };

export function dataSource(): DataSource {
  const configured = process.env.ALPHADOTS_API_URL?.trim();
  if (!configured) return { kind: "snapshot" };
  let upstream: URL;
  try {
    upstream = new URL(configured);
  } catch {
    throw new Error("ALPHADOTS_API_URL is not a valid URL");
  }
  if (upstream.protocol !== "https:" && upstream.protocol !== "http:") {
    throw new Error("ALPHADOTS_API_URL must be an http(s) URL");
  }
  return { kind: "live", upstream };
}

// Read once per server instance. The path is listed in `outputFileTracingIncludes`
// (next.config.ts) so the file ships inside the serverless function bundle.
const SNAPSHOT_PATH = path.join(process.cwd(), "src", "data", "snapshot.json");
let snapshot: Promise<Snapshot> | undefined;

export function loadSnapshot(): Promise<Snapshot> {
  snapshot ??= readFile(SNAPSHOT_PATH, "utf-8").then((text) => JSON.parse(text) as Snapshot);
  return snapshot;
}
