"use client";

import { useEffect, useSyncExternalStore } from "react";

export type Theme = "system" | "light" | "dark";

const STORAGE_KEY = "alphadots-theme";
const CHANGE_EVENT = "alphadots-theme-change";

function read(): Theme {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system"; // storage blocked: follow the operating system
  }
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGE_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGE_EVENT, onChange);
  };
}

/** Theme preference: follows the OS unless the viewer picks light or dark. */
export function useTheme(): [Theme, (next: Theme) => void] {
  const theme = useSyncExternalStore<Theme>(subscribe, read, () => "system");

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") delete root.dataset.theme;
    else root.dataset.theme = theme;
  }, [theme]);

  const setTheme = (next: Theme) => {
    try {
      if (next === "system") window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // The preference cannot be saved; still apply it for this page view.
      if (next === "system") delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = next;
    }
    window.dispatchEvent(new Event(CHANGE_EVENT));
  };
  return [theme, setTheme];
}
