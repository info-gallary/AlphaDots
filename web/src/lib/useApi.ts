"use client";

import { useEffect, useState } from "react";

import type { ApiErrorBody } from "./types";

export type Params = Record<string, string | number | boolean | null | undefined>;

export interface ApiFailure {
  status: number;
  code: string;
  message: string;
}

interface Settled<T> {
  key: string;
  data?: T;
  error?: ApiFailure;
}

export interface ApiState<T> {
  /** Last successful payload. Kept while a newer request is in flight. */
  data: T | undefined;
  error: ApiFailure | undefined;
  /** No payload has arrived yet for any request. */
  isLoading: boolean;
  /** `data` belongs to an earlier request (filters or as-of changed, refetch pending). */
  isStale: boolean;
}

export function buildUrl(path: string, params: Params = {}): string {
  const query = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") query.set(name, String(value));
  }
  const suffix = query.toString();
  return `/api/${path.replace(/^\/+/, "")}${suffix ? `?${suffix}` : ""}`;
}

async function request<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, cache: "no-store" });
  if (!response.ok) {
    let failure: ApiFailure = {
      status: response.status,
      code: "http_error",
      message: `Request failed with status ${response.status}.`,
    };
    try {
      const body = (await response.json()) as ApiErrorBody;
      failure = { status: response.status, code: body.error.code, message: body.error.message };
    } catch {
      // Non-JSON error body: keep the generic failure.
    }
    throw failure;
  }
  return (await response.json()) as T;
}

/**
 * Fetch a JSON resource and optionally poll it.
 *
 * The previous payload stays on screen while a new one loads, flagged `isStale`, so
 * changing a filter or the as-of time never flashes a skeleton or shifts the layout.
 * Pass `path = null` to skip the request.
 */
export function useApi<T>(
  path: string | null,
  params: Params = {},
  options: { refreshMs?: number } = {},
): ApiState<T> {
  const url = path === null ? null : buildUrl(path, params);
  const refreshMs = options.refreshMs ?? 0;
  const [settled, setSettled] = useState<Settled<T>>({ key: "" });

  useEffect(() => {
    if (url === null) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    const load = async () => {
      try {
        const data = await request<T>(url, controller.signal);
        setSettled({ key: url, data });
      } catch (failure) {
        if (controller.signal.aborted) return;
        const error: ApiFailure =
          typeof failure === "object" && failure !== null && "code" in failure
            ? (failure as ApiFailure)
            : { status: 0, code: "network_error", message: "The dashboard server is not reachable." };
        // Keep the last good payload on screen; only the error state changes.
        setSettled((previous) => ({ key: url, data: previous.data, error }));
      }
      if (refreshMs > 0 && !controller.signal.aborted) timer = setTimeout(load, refreshMs);
    };

    void load();
    return () => {
      controller.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [url, refreshMs]);

  if (url === null) {
    return { data: undefined, error: undefined, isLoading: false, isStale: false };
  }
  const current = settled.key === url;
  return {
    data: settled.data,
    error: current ? settled.error : undefined,
    isLoading: settled.data === undefined && settled.error === undefined,
    isStale: !current && settled.data !== undefined,
  };
}
