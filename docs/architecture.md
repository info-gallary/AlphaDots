# Architecture (milestone M0)

This describes what is built today. The full target architecture, and the order it is
built in, are in [`CLAUDE.md`](../CLAUDE.md).

## Event flow

```text
data/fixtures/news/*.jsonl   (synthetic, CC0)
        │  FixtureReplayAdapter + MergedAdapter   (arrival order, event time)
        ▼
[ingestion]      ──► news.raw.v1             poison lines ──► events.dead_letter.v1
        ▼
[preprocessor]   clean text · exact dedup · issuer resolution (effective-dated tickers)
        ──► events.normalized.v1
        ▼
[sentiment]      lexicon baseline · event type · materiality prior · abstention
        ──► sentiment.scored.v1
        ▼
[signal_engine]  S(i,t) = Σ relevance·materiality·signed·exp(−age/τ), inputs ≤ t
        ──► signals.generated.v1
        ▼
[api]            projection consumer → read model (Postgres / SQLite) → REST /v1
        ▼
[web]            Next.js dashboard  (live proxy, or bundled snapshot on Vercel)
```

Every arrow is a versioned topic on Redpanda (Kafka protocol). Each service has its own
consumer group and its own tables (`pre_*`, `sig_*`, `api_*`); no service reads
another's tables, and `tests/unit/test_repository_rules.py` enforces that no service
imports another.

## Contracts

`libs/contracts` defines one Pydantic model per topic. The rules:

- The topic name carries the major version. Breaking changes need a new topic.
- Within a major version changes are additive; consumers ignore unknown fields.
- Timestamps must be timezone-aware and are normalised to UTC. Naive values are rejected.
- `first_seen_at <= ingested_at <= available_at` holds on every source-derived event.
- A signal's contributions must all have `available_at <= as_of`, or the message is
  invalid.

Committed JSON Schema snapshots (`libs/contracts/schemas/`) are the baseline.
`scripts/export_schemas.py` refuses to overwrite one with a breaking change, and
`tests/contract` checks compatibility and freshness.

## Time and availability

`available_at` is when a consumer could have used a message. In replay mode it is
derived from recorded event time, never the wall clock, so replays are deterministic:

| Stage | `available_at` |
|---|---|
| ingestion | `first_seen_at` + 1 s |
| preprocessor | upstream + 1 s |
| sentiment | upstream + 1 s |
| signal | `as_of` + 1 s, where `as_of` is the triggering score's `available_at` |

The 1-second allowances are a stated modelling assumption
(`ALPHADOTS_STAGE_LATENCY_ALLOWANCE_SECONDS`), not a measurement. Their job is to make
sure no point-in-time filter assumes zero processing time.

Every API read takes `as_of` and applies `available_at <= as_of` to each table
separately, so an event can be visible while its score, available one second later, is
not. `tests/temporal_leakage` injects future data on purpose and asserts it changes
nothing.

## Delivery and idempotency

- **At-least-once.** A worker publishes outputs first, then commits the input offset. A
  crash between the two redelivers the input.
- **Deterministic IDs.** Every output ID is a hash of its inputs, so reprocessing
  produces byte-identical messages and downstream upserts are no-ops.
- **Failure policy** (`libs/common/.../worker.py`):
  - Invalid JSON or schema: quarantined immediately.
  - Handler errors: retried with jittered backoff, then quarantined.
  - Infrastructure errors (database down): crash and redeliver, never quarantine.
  - A dead letter that is itself unprocessable is dropped and logged, so the dead-letter
    topic cannot loop.
- **Order independence.** Processing out of event-time order (backfill, multi-file
  replay) cannot change results. When an event arrives, the signal engine restates every
  later signal whose window contains it, under the same ID. Tests shuffle arrival order
  and require identical final signals.

Known limit: dedup keeps the first *processed* copy as canonical. Making that
order-independent needs the watermark policy planned for M1.

## Signals are not predictions

The signal engine summarises recent news tone. `τ`, the stance threshold and the minimum
evidence weight are configured priors, not estimates. Every signal says so in
`params.tau_source` and `validation_status = unvalidated_heuristic`, and the dashboard
says so next to every score. Return labels, walk-forward validation and any claim of
predictive value belong to M3.

## The dashboard

`web/` is Next.js (App Router, TypeScript, no UI framework). The browser only calls the
app's own `/api/*` routes, which either proxy to the API or answer from the bundled
snapshot. See [`deployment-vercel.md`](deployment-vercel.md) and
[ADR 0002](adr/0002-dashboard-snapshot-mode.md).

Chart colours come from a validated palette: polarity uses a blue/red diverging pair
checked for colour-vision-deficiency separation and contrast in both themes. Status
colours always come with a glyph and a label.

## What is not built yet

Market data and features (M3), FinBERT (M2), the research agent (M4), Kubernetes, Helm,
Terraform, CI/CD and the threat model (M5), and benchmarks (M6). `scripts/tasks.py`
lists the specification's `make` targets that do not exist yet, rather than stubbing
them.
