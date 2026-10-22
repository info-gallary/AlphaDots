# ADR 0002: Serve the dashboard from a parity-tested snapshot when no API is attached

- Status: accepted
- Date: 2026-10-17

## Context

The dashboard must be deployable to Vercel. Vercel runs request-scoped functions. It
cannot host Redpanda or PostgreSQL, and it cannot run the long-lived Kafka consumers that
feed the API's read model. A dashboard deployed alone therefore needs either an
externally hosted API or its own data.

## Options considered

1. **Require an external API.** Honest and simple, but a fresh deployment shows nothing
   until the pipeline is hosted somewhere reachable.
2. **Run the FastAPI app as a Vercel Python function over a bundled SQLite read model.**
   It reuses the Python read code, but adds a second project, workspace packaging that
   Vercel's Python builder does not support directly, and a deployment path that cannot be
   verified without deploying.
3. **Bundle the read model as JSON and answer queries in the Next.js server**, with
   recorded responses from the real API as the specification of correct behaviour.

## Decision

Option 3, with option 1 kept as "live mode".

- `scripts/export_snapshot.py` builds the snapshot by running the real pipeline over the
  fixtures. It also records the real API's responses to a fixed set of 36 requests.
- `web/src/server/snapshot-api.ts` implements the same read rules, most importantly
  `available_at <= as_of` per table. `web/test/parity.test.ts` requires identical
  responses, error paths included.
- A Python test fails when the committed snapshot no longer matches the pipeline.
- Setting `ALPHADOTS_API_URL` switches the same routes to proxying a real API.

## Consequences

- A fresh Vercel deployment works with zero configuration and is clearly labelled as a
  simulated snapshot.
- The read rules now exist twice, in Python and TypeScript. The parity test is what
  keeps that safe. Any change to the API's read semantics must be followed by
  `make snapshot` and must keep `pnpm test` passing.
- Snapshot mode shows nothing new until the snapshot is re-exported. That is acceptable
  for a demo and is stated in the UI.
