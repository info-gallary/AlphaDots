"use client";

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import type { Feed, OverviewResponse } from "@/lib/types";
import { useApi, type ApiFailure } from "@/lib/useApi";

/** Poll intervals while following the feed, while pinned, and for a static snapshot. */
const LIVE_REFRESH_MS = 2500;
const PINNED_REFRESH_MS = 15000;

export interface SourceInfo {
  source: "live" | "snapshot";
  snapshot?: {
    description: string;
    generated_by: string;
    models: Record<string, string>;
    fixtures: string[];
    events: number;
    signals: number;
  };
}

interface AppState {
  /** ISO timestamp the whole dashboard is pinned to, or null to follow the latest data. */
  asOf: string | null;
  setAsOf: (value: string | null) => void;
  /** Event-time range of the feed, always for the latest data regardless of `asOf`. */
  feed: Feed | undefined;
  source: SourceInfo | undefined;
  apiError: ApiFailure | undefined;
  /** How often views should re-poll; 0 when the data cannot change. */
  refreshMs: number;
}

const Context = createContext<AppState | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [asOf, setAsOf] = useState<string | null>(null);
  const source = useApi<SourceInfo>("source").data;
  const isSnapshot = source?.source === "snapshot";
  // A snapshot never changes, so nothing polls it.
  const latest = useApi<OverviewResponse>("v1/overview", {}, {
    refreshMs: isSnapshot ? 0 : LIVE_REFRESH_MS,
  });
  const feed = latest.data?.feed;
  const apiError = latest.error;
  const refreshMs = isSnapshot ? 0 : asOf === null ? LIVE_REFRESH_MS : PINNED_REFRESH_MS;

  const value = useMemo<AppState>(
    () => ({ asOf, setAsOf, feed, source, apiError, refreshMs }),
    [asOf, feed, source, apiError, refreshMs],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useAppState(): AppState {
  const state = useContext(Context);
  if (state === null) throw new Error("useAppState must be used inside AppStateProvider");
  return state;
}
