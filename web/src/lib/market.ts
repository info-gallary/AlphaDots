/** Which listing universe an instrument belongs to, from its stable instrument_id prefix. */

export type Market = "replay" | "nse";

export const MARKET_LABEL: Record<Market, string> = {
  replay: "Replay universe",
  nse: "NSE India",
};

export function marketOf(instrumentId: string): Market {
  return instrumentId.startsWith("NSE-") ? "nse" : "replay";
}
