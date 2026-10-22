/** Formatting helpers. Every time shown in the UI is UTC, and says so. */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MINUS = "−";

function pad(value: number): string {
  return value.toString().padStart(2, "0");
}

/** `Mar 02 13:30:06` */
export function formatTime(iso: string): string {
  const date = new Date(iso);
  return `${formatDay(iso)} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`;
}

/** `13:30:06` */
export function formatClock(iso: string): string {
  const date = new Date(iso);
  return `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`;
}

/** `Mar 02` */
export function formatDay(iso: string): string {
  const date = new Date(iso);
  return `${MONTHS[date.getUTCMonth()]} ${pad(date.getUTCDate())}`;
}

/** `Mar 02 13:30` */
export function formatMinute(iso: string): string {
  const date = new Date(iso);
  return `${formatDay(iso)} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
}

/** `4s`, `4m 45s`, `4h 45m`, `2d 3h` */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.round(Math.abs(totalSeconds));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${pad(seconds % 60)}s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ${pad(minutes % 60)}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

/** Round configuration spans: `24 hours`, `5 days`, `90 minutes`. */
export function formatSpan(totalSeconds: number): string {
  const plural = (count: number, unit: string) => `${count} ${unit}${count === 1 ? "" : "s"}`;
  if (totalSeconds % 86_400 === 0 && totalSeconds >= 2 * 86_400) return plural(totalSeconds / 86_400, "day");
  if (totalSeconds % 3600 === 0) return plural(totalSeconds / 3600, "hour");
  if (totalSeconds % 60 === 0) return plural(totalSeconds / 60, "minute");
  return formatDuration(totalSeconds);
}

/** `+0.75`, `−0.80`, `0.00` with a true minus sign. */
export function formatSigned(value: number, digits = 2): string {
  const rounded = Number(value.toFixed(digits));
  if (rounded === 0) return (0).toFixed(digits);
  return `${rounded > 0 ? "+" : MINUS}${Math.abs(rounded).toFixed(digits)}`;
}

export function formatNumber(value: number): string {
  return value.toLocaleString("en-US");
}

export function formatPercent(part: number, whole: number): string {
  if (whole <= 0) return "0%";
  return `${Math.round((part / whole) * 100)}%`;
}

/** `no_lexicon_match` → `No lexicon match` */
export function humanize(code: string): string {
  const words = code.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function shortId(id: string, keep = 10): string {
  const [prefix, rest] = id.includes("_") ? id.split(/_(.+)/) : ["", id];
  const body = rest ?? id;
  return body.length > keep ? `${prefix ? `${prefix}_` : ""}${body.slice(0, keep)}…` : id;
}

export const SOURCE_NAMES: Record<string, string> = {
  synthetic_wire_a: "Wire A",
  synthetic_wire_b: "Wire B",
};

export function sourceName(source: string): string {
  return SOURCE_NAMES[source] ?? source;
}
