/** Which listing universe an instrument belongs to, from its stable instrument_id prefix. */

export type Market = "us" | "nse" | "replay";

export const MARKET_LABEL: Record<Market, string> = {
  us: "US (SEC filers)",
  nse: "India (NSE)",
  replay: "Synthetic replay",
};

export const MARKET_ORDER: Market[] = ["us", "nse", "replay"];

export function marketOf(instrumentId: string): Market {
  if (instrumentId.startsWith("SEC-")) return "us";
  if (instrumentId.startsWith("NSE-")) return "nse";
  return "replay";
}
