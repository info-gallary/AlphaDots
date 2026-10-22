# ADR 0001: Scope and simplifications of the M0 vertical slice

- Status: accepted
- Date: 2026-10-13

## Context

M0 asks for one synthetic headline to reach the API through typed ingest and
normalisation events, with `make up`, `make seed`, `make smoke` and `make test` passing.
The specification also defines `make smoke` as "ingest → score → signal → API", and the
implementation order is mandatory unless a reason is recorded. This ADR is that record.

## Decisions

1. **M0 includes two thin, deterministic stages beyond normalisation:** a lexicon
   sentiment baseline and a decay-heuristic signal. Without them the smoke target cannot
   be met and the dashboard has nothing to show. Both sit on the specification's model
   ladder (rung 1 and the interpretable `S(i,t)` candidate), need no model download, and
   are labelled unvalidated everywhere. FinBERT remains M2; anything predictive remains M3.
2. **The API hosts its own projection consumer** in the same process as the HTTP server.
   The read model is still owned only by the API service, and the process exits if the
   consumer dies, so a stale API cannot keep serving. Splitting it out waits until
   scaling needs it.
3. **The instrument master is an immutable, versioned file** read by the services that
   need it. That keeps it out of the mutable-shared-table pattern the specification
   forbids. An effective-dated reference topic replaces it in M1.
4. **Tables are created with `metadata.create_all`, not Alembic.** No schema has been
   released yet, so there is nothing to migrate. Alembic arrives with the first schema
   change that needs one.
5. **One partition per topic.** Deterministic dedup across partitions needs a
   partition-key design, which belongs with M1's watermark work.
6. **Replay uses recorded event time, and trace IDs are derived from event IDs.** Replays
   are byte-identical, which is what makes idempotency testable. Live ingestion (M1) will
   use wall-clock observation times and random trace IDs.
7. **All services share one PostgreSQL database locally, each with its own tables.**
   Per-service schemas and credentials come with the M5 deployment work.

## Consequences

The slice is end-to-end and testable today. Each simplification is named in code where
it applies, and has a milestone that removes it.
