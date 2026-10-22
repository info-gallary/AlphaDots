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

/*
 * The one write the dashboard makes: asking the research agent for a brief. Only this exact
 * path is accepted, only a JSON body (a cross-site HTML form cannot send one without a CORS
 * preflight, which this origin never grants), and only the two fields the API accepts are
 * forwarded. The agent is bounded server-side (tool calls, tokens, time, cost) regardless.
 */
const RESEARCH_JOBS_PATH = "v1/research/jobs";
const MAX_BODY_BYTES = 1024;
const SYMBOL_RE = /^[A-Za-z0-9.-]{1,16}$/;

function researchJobBody(raw: string): { symbol: string; as_of: string | null } | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const { symbol, as_of: asOf } = parsed as Record<string, unknown>;
  if (typeof symbol !== "string" || !SYMBOL_RE.test(symbol)) return null;
  if (asOf !== undefined && asOf !== null && (typeof asOf !== "string" || Number.isNaN(Date.parse(asOf)))) {
    return null;
  }
  return { symbol: symbol.toUpperCase(), as_of: typeof asOf === "string" ? asOf : null };
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await context.params;
  const trace = traceId();
  if (path.join("/") !== RESEARCH_JOBS_PATH) return errorResponse(404, "not_found", "Not Found", trace);
  if (request.headers.get("sec-fetch-site") === "cross-site") {
    return errorResponse(403, "forbidden", "Cross-site requests are not accepted.", trace);
  }
  if (!(request.headers.get("content-type") ?? "").startsWith("application/json")) {
    return errorResponse(415, "unsupported_media_type", "Send the request as application/json.", trace);
  }

  const source = dataSource();
  if (source.kind !== "live") {
    return errorResponse(
      503,
      "agent_unavailable",
      "The research agent runs against a live AlphaDots API. Point ALPHADOTS_API_URL at one started with the agents profile.",
      trace,
    );
  }

  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) return errorResponse(413, "payload_too_large", "Request body is too large.", trace);
  const body = researchJobBody(raw);
  if (body === null) {
    return errorResponse(422, "invalid_request", "Expected {\"symbol\": \"TICKER\", \"as_of\": optional ISO time}.", trace);
  }

  const headers: Record<string, string> = { accept: "application/json", "content-type": "application/json" };
  const token = process.env.ALPHADOTS_API_TOKEN;
  if (token) headers.authorization = `Bearer ${token}`;
  const base = source.upstream.href.replace(/\/+$/, "");
  try {
    const response = await fetch(`${base}/${RESEARCH_JOBS_PATH}`, {
      method: "POST",
      headers,
      body: JSON.stringify(body.as_of ? body : { symbol: body.symbol }),
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
    return errorResponse(502, "upstream_unavailable", "The AlphaDots API is not reachable.", trace);
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
