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

## Datasets

Every service reads its instrument master and news files from one dataset directory,
`ALPHADOTS_FIXTURES_DIR`, so the same code runs on either:

- `data/fixtures/`: synthetic and deterministic, one line per behaviour to exercise. The
  tests, `make seed` and `make smoke` use it. Feed mode `simulated`.
- `data/real/`: SEC EDGAR 8-K and 6-K filings for 45 real companies, captured by
  `make capture` through the same polite, cached EDGAR client the earnings study uses.
  `make demo` and the Vercel snapshot use it. Feed mode `delayed`, because it is a
  backfill from the archive; a live EDGAR poller would make it `live`.

The ingestion adapter does not care which: both are replayed in arrival order with their
recorded times, so a replay is byte-identical every time.

## Offline research

Training and evaluation are offline jobs, not services. `research/` holds two studies:

- **Simulation study** (`alphadots_research.study`): a synthetic market with a planted
  effect and a null twin, used to show the evaluation finds real effects, stays silent
  on none, and that a look-ahead bug manufactures returns. It calls the live
  `decayed_sentiment`, so the point-in-time guard under test is the production one.
- **Earnings study** (`alphadots_research.earnings`): real SEC 8-K press releases, timed
  to EDGAR's acceptance second, against daily returns. The plan is
  [pre-registered](research/earnings-tone-preregistration.md). `make earnings-data`
  downloads through a rate-limited, caching client into `var/research` (gitignored);
  `make earnings-study` runs fully offline from that cache and writes only aggregates to
  `web/src/data/earnings-study.json`.

```text
EDGAR submissions ─▶ 8-K Item 2.02 ─▶ EX-99 text ─▶ LM tone ─▶ tone surprise (own history)
EDGAR acceptance time ─▶ day 0 = first close after publication
Yahoo closes + Fama-French factors ─▶ abnormal returns ─▶ CARs, regressions, portfolio
```

The same availability rule as the live pipeline applies: a release can only be acted on
at a close after EDGAR accepted it. The study runs the date-only timing too, to measure
what ignoring that rule would have reported.

## The dashboard

`web/` is Next.js (App Router, TypeScript, no UI framework). The browser only calls the
app's own `/api/*` routes, which either proxy to the API or answer from the bundled
snapshot. See [`deployment-vercel.md`](deployment-vercel.md) and
[ADR 0002](adr/0002-dashboard-snapshot-mode.md).

Chart colours come from a validated palette: polarity uses a blue/red diverging pair
checked for colour-vision-deficiency separation and contrast in both themes. Status
colours always come with a glyph and a label.

## Dashboard views

| View | What it answers |
|---|---|
| Overview (`/`) | Which issuers lean which way (signal leaderboard), the tone mix, day-by-day news flow, an issuer × day sentiment heatmap, and the most positive and most negative headlines. |
| Event tape (`/tape`) | Every accepted item, newest first, with provenance and availability times. |
| Issuers (`/issuers`) | Latest score and path per issuer, filterable by market (replay universe, NSE India). |
| Research (`/research`) | The research agent's brief for an issuer: cited bull and bear cases, abstention, run telemetry. |
| Earnings study (`/earnings`) | The real-data study: abnormal returns around releases by tone surprise, the pre-registered tests and decision, the timing trap, sample construction and provenance. |
| Backtest (`/backtest`) | The simulation study: planted vs null world, walk-forward folds, costs, the look-ahead control. |
| Pipeline (`/system`) | Stage throughput, the monitor agent's findings, the dead-letter queue, milestones. |

Every chart has a table view, so no value is reachable only by hovering.

## What is not built yet

Streaming market data and live features (M3; the earnings study uses daily closes
offline), FinBERT (M2), a streaming SEC adapter and watermark policy (M1; the earnings
study's EDGAR client is batch and offline), and benchmarks (M6). Kubernetes is linted and templated but no live k3d cluster has run
on the development machine (see the threat model's known gaps), and the AWS Terraform has
been validated, never applied. `scripts/tasks.py` lists the specification's `make`
targets that do not exist yet, rather than stubbing them.
