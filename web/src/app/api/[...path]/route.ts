import type { NextRequest } from "next/server";

import { handleSnapshotRequest } from "@/server/snapshot-api";
import { dataSource, loadSnapshot } from "@/server/source";

/**
 * Read-only data endpoint for the dashboard.
 *
 * The browser only ever calls `/api/...` on its own origin. Depending on configuration
 * this either proxies to a running AlphaDots API (live) or answers from the bundled
 * read-model snapshot (see `src/server/source.ts`). Either way there is no CORS surface,
 * any bearer token stays on the server, and the upstream origin is fixed by configuration,
 * so the route cannot be used as an open proxy.
 */

const ALLOWED_PATH = /^(?:v1\/[A-Za-z0-9._~-]+(?:\/[A-Za-z0-9._~-]+)*|health\/(?:live|ready))$/;
const UPSTREAM_TIMEOUT_MS = 10_000;
// Snapshot answers are immutable for the life of a deployment, so the CDN may keep them.
const SNAPSHOT_CACHE = "public, max-age=0, s-maxage=3600, stale-while-revalidate=86400";

export const dynamic = "force-dynamic";

function traceId(): string {
  return crypto.randomUUID().replace(/-/g, "");
}

function errorResponse(status: number, code: string, message: string, trace: string): Response {
  return Response.json(
    { error: { code, message, trace_id: trace, details: null } },
    { status, headers: { "cache-control": "no-store", "x-trace-id": trace } },
  );
}

async function fromUpstream(upstream: URL, segments: string[], request: NextRequest) {
  const base = upstream.href.replace(/\/+$/, "");
  const target = new URL(`${base}/${segments.map(encodeURIComponent).join("/")}`);
  request.nextUrl.searchParams.forEach((value, key) => target.searchParams.append(key, value));

  const headers: Record<string, string> = { accept: "application/json" };
  const token = process.env.ALPHADOTS_API_TOKEN;
  if (token) headers.authorization = `Bearer ${token}`;

  try {
    const response = await fetch(target, {
      headers,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
    return new Response(response.body, {
      status: response.status,
      headers: {
        "content-type": response.headers.get("content-type") ?? "application/json",
        "cache-control": "no-store",
        "x-trace-id": response.headers.get("x-trace-id") ?? "",
        "x-alphadots-source": "live",
      },
    });
  } catch {
    return errorResponse(
      502,
      "upstream_unavailable",
      "The AlphaDots API is not reachable. Check ALPHADOTS_API_URL, or unset it to serve the bundled snapshot.",
      traceId(),
    );
  }
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await context.params;
  const trace = traceId();
  if (!ALLOWED_PATH.test(path.join("/")) || path.some((part) => part === "." || part === "..")) {
    return errorResponse(404, "not_found", "Not Found", trace);
  }

  const source = dataSource();
  if (source.kind === "live") return fromUpstream(source.upstream, path, request);

  const result = handleSnapshotRequest(
    await loadSnapshot(),
    path,
    request.nextUrl.searchParams,
    new Date(),
    trace,
  );
  return Response.json(result.body, {
    status: result.status,
    headers: {
      "cache-control": result.status === 200 ? SNAPSHOT_CACHE : "no-store",
      "x-trace-id": trace,
      "x-alphadots-source": "snapshot",
    },
  });
}
