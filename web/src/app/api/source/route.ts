import { dataSource, loadSnapshot } from "@/server/source";

/** Tells the UI whether it is showing a live API or the bundled snapshot. */

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  const source = dataSource();
  if (source.kind === "live") {
    // The upstream address is deliberately not disclosed to the browser.
    return Response.json({ source: "live" }, { headers: { "cache-control": "no-store" } });
  }
  const snapshot = await loadSnapshot();
  return Response.json(
    {
      source: "snapshot",
      snapshot: {
        description: snapshot.description,
        generated_by: snapshot.generated_by,
        models: snapshot.models,
        fixtures: Object.keys(snapshot.fixtures),
        events: snapshot.events.length,
        signals: snapshot.signals.length,
      },
    },
    { headers: { "cache-control": "public, max-age=0, s-maxage=3600" } },
  );
}
