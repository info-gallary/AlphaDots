# CLAUDE.md — AlphaDots

> **Project:** AlphaDots — Real-Time Agentic Market Sentiment Analysis & Equity Research Platform  
> **Mission:** Build a reproducible, point-in-time-correct research system that turns financial news, disclosures, fundamentals, and market data into explainable sentiment features, testable equity signals, and evidence-backed agentic research reports.  
> **Audience:** Quant researchers, quant analysts, quant developers, and systematic-trading engineering interviewers.  
> **Status:** Engineering specification and instructions for Claude Code, **not** a claim that the platform or its trading performance already exists.

## 0. Operating instructions for Claude Code

You are the principal **quant research engineer, applied-ML researcher, and platform architect** implementing AlphaDots. Favor **research validity, data correctness, production engineering, and verifiable results** over flashy demos.

1. **Before coding:** inspect the repository, identify what exists, and produce a short plan naming files to change, acceptance tests, and assumptions. Implement a small end-to-end vertical slice before adding more services.
2. **Never fabricate** model accuracy, Sharpe ratios, P&L, latency results, data access, backtest results, citations, benchmark outputs, or completed infrastructure. Mark performance requirements as **targets until measured**.
3. **Prioritize correctness:** no look-ahead bias, survivorship bias, data leakage, post-publication timestamp manipulation, mislabeled instruments, silently missing data, or in-sample performance presented as out-of-sample.
4. **Only use data legally accessible under provider terms.** Do not scrape around paywalls, evade rate limits, imply exchange-real-time access without a licensed feed, or commit proprietary datasets.
5. **Make the local system run without paid credentials:** synthetic data, fixture disclosures, delayed/licensed sample files, deterministic market replay, and mock LLM adapters must work end to end.
6. **Keep the live research signal path deterministic:** LLM agents may explain, critique, and summarize signals, but they may not alter historical data, autonomously place trades, or override the quantitative model.
7. **Build for constrained developer hardware:** CPU-first smoke tests, optional CUDA support, configurable inference models, reduced local resource profiles, and fast unit tests. Avoid requiring a cloud account to develop.
8. **Engineering discipline:** typed code, versioned contracts, idempotent processing, structured logging, metrics, traces, documented threat model, CI checks, and automated tests in every increment.
9. **At the end of each task**, report changed files, how to run them, commands actually executed, exact test results or blockers, and next incremental step. Do not claim successful commands unless executed.
10. **Do not attempt the full architecture in a single untestable change.** Follow the milestones and definition of done below. Preserve simple designs until benchmarks justify extra complexity.

## 1. Product and research questions

AlphaDots should answer questions such as:

- **What happened?** Detect a new earnings filing, management guidance change, macro headline, product launch, regulatory event, or sector-wide news shock.
- **Which tradable firms are affected?** Entity resolution maps mentions to unique instrument identifiers; handle name collisions, ticker changes, subsidiaries, and multi-company events.
- **How is the market likely to react?** Score financial polarity, materiality, novelty, uncertainty, sector exposure, and decay of information; estimate specific, versioned prediction horizons.
- **Is the signal statistically/economically credible?** Compare out-of-sample performance to price-only, simple lexicon/FinBERT, and randomized or lagged-sentiment baselines with realistic frictions.
- **Why does the system believe it?** Generate traceable equity research briefs with original-source links, evidence timestamps, quoted facts, quantitative estimates, risk considerations, and a clear *abstain* option.
- **Can it operate reliably?** Stream and replay events, tolerate transient failures, expose observable SLIs, and reproduce an entire experiment from committed configurations.

**Non-goals for the initial release:** brokerage integration, discretionary autonomous execution, personalized investment advice, promises of profitable alpha, proprietary HFT feed performance, and unbounded open-ended agent browsing.

## 2. Required stack — cover ALL seven selected areas

| Checked requirement | Selected implementation | Evidence required in repository |
|---|---|---|
| **Microservices** | Python 3.12, FastAPI, asyncio, Kafka-compatible Redpanda, typed events, REST/WebSocket API | Independently buildable services; contracts; isolated tests; event flow diagram |
| **Docker** | Multi-stage Dockerfiles, `docker compose` profiles, non-root images, health checks | `make up`, `make smoke`, image build/replay from clean checkout |
| **Infrastructure as Code (IaC)** | Terraform for **AWS** reference environment; separate local deployment configuration | Validated Terraform modules, remote-state guidance, variables, outputs, cost/destroy notes |
| **Kubernetes** | `k3d` local cluster, Helm chart(s), HPA where justified, readiness/liveness probes | `make k8s-up`, Helm lint/template, deploy/restart demonstration |
| **CI/CD** | GitHub Actions: PR verification, container publish, staged deploy with approval | Workflow YAML, pinned tooling, cached tests, gated deployment, rollback runbook |
| **DevSecOps** | Gitleaks, Semgrep, pip-audit, Trivy, Checkov, SBOM, OIDC, least-privilege IAM | Enforced checks, threat model, secret handling, signed/pinned release artifact policy |
| **Cloud provider / local machine** | **Local-first** Docker/k3d; optional **AWS** EKS + ECR + RDS + S3 + Secrets Manager | Local no-credential demo and documented AWS Terraform plan/deploy/destroy procedure |

### Opinionated dependencies (avoid tool sprawl)

- **Backend:** Python 3.12, `uv`, FastAPI, Pydantic v2, SQLAlchemy/Alembic, `pytest`, `ruff`, `mypy`.
- **Streaming:** Redpanda (Kafka protocol), consumer groups, retries, dead-letter queues, explicit offset control.
- **Persistence:** PostgreSQL for metadata/point-in-time records; `pgvector` for bounded RAG retrieval; Redis for cache/rate limits; Parquet + local MinIO / cloud S3 for immutable raw and analytical datasets; DuckDB for offline research.
- **ML:** PyTorch, Hugging Face Transformers (FinBERT baseline), scikit-learn, LightGBM, MLflow experiment/model registry. GPU optimization is optional.
- **Agentic workflow:** LangGraph state graph with Pydantic-validated inputs/outputs, a pluggable hosted/open-weight LLM adapter, and a deterministic mock adapter for CI.
- **Frontend:** Next.js + TypeScript with live event monitor, equity workspace, signal history, report provenance, and backtest dashboard.
- **Observability:** OpenTelemetry, Prometheus, Grafana; add tracing backend only after basic trace propagation works.
- **Infrastructure:** Docker Compose, Helm, k3d, Terraform, GitHub Actions. Use AWS only when specifically configured.
- **Optional quantitative developer extension:** a C++20 market-event parser/normalizer and latency benchmark **only if** a measured Python bottleneck warrants it; prove identical message contracts and correctness first.

Keep services scalable by responsibility, not for artificial microservice count. **Training and backtesting are offline jobs, not latency-sensitive services.**

## 3. Architecture and event flow

```text
LICENSED / PUBLIC / SYNTHETIC SOURCES
  SEC EDGAR filings + company IR | permitted financial news
  allowed historical/delayed market bars | fundamentals | fixture replay
                        |
                 [Ingestion Service]
         source adapters, rate limits, offsets
                        |
               news.raw.v1 / filings.raw.v1
                        |
              [Preprocessing Service]
       dedup -> language/text cleanup -> NER
       entity/ticker resolution -> provenance
                        |
                 events.normalized.v1
                        |
        +---------------+----------------+
        |                                |
 [Sentiment Model Service]       [Market Feature Service]
 FinBERT -> uncertainty,        as-of prices, liquidity,
 event type, novelty,           factor/sector exposures,
 materiality, embeddings        point-in-time fundamentals
        |                                |
   sentiment.scored.v1           features.ready.v1
        +---------------+----------------+
                        |
                [Signal Engine]
     versioned features -> LightGBM baselines
     regime controls -> uncertainty -> risk flags
                        |
                signals.generated.v1
                        |
                [Research Agent]
  retrieve -> analyze -> challenge -> verify -> cite
                        |
              reports.generated.v1
                        |
        [API + WebSocket Gateway] <-> [Next.js UI]
                        |
    Watchlists | equities | signals | evidence | reports

Cross-cutting: PostgreSQL | Redis | Parquet/MinIO or S3
            MLflow | OTel/Prometheus/Grafana | DLQ
Offline: immutable snapshot -> training -> purged walk-forward
         -> backtesting -> experiment reports -> registry
```

### Service boundaries

1. **`ingestion`** — adapter interface for SEC/company disclosures, permitted news, market bars, and deterministic replay. Maintain provider checkpoints, per-source rate limits, timeouts, backoff, terms/attribution metadata, and poison-event quarantine.
2. **`preprocessor`** — Unicode and timestamp standardization, language detection, content deduplication, semantic event clustering, company/entity disambiguation, publication-to-availability tracking, audit-ready source records.
3. **`sentiment`** — batched FinBERT inference; probabilities for negative/neutral/positive, entropy/uncertainty, event materiality and relevance, sector-specific aggregates; model ID and calibration version on each event.
4. **`market_features`** — as-of joins with market/fundamental data, technical/liquidity/sector exposures, feature freshness checks, calendar/corporate-action handling, non-tradable-period controls.
5. **`signal_engine`** — transparent signal generation with deterministic heuristics baseline and optional trained ranking/classification model; horizons, timestamps, confidence/calibration, coverage, abstention reasons and risk flags.
6. **`research_agent`** — bounded agent graph that creates evidence-backed reports from already computed signals and approved retrieval tools; outputs structured JSON + human-readable report.
7. **`api`** — FastAPI read layer + JWT/OIDC integration-ready access control, paginated REST resources, authenticated WebSocket/SSE feed, watchlist and instrument lookups, API docs.

**Never share mutable database tables as implicit service APIs.** Services own their data, publish versioned messages, and expose explicit read contracts. Consolidating services is acceptable for the first local vertical slice; keep boundaries testable.

## 4. Data contracts and temporal integrity — NON-NEGOTIABLE

Version every Kafka topic and every Pydantic message schema. At minimum:

```text
news.raw.v1
filings.raw.v1
events.normalized.v1
sentiment.scored.v1
market.bars.v1
features.ready.v1
signals.generated.v1
reports.generated.v1
events.dead_letter.v1
```

**Canonical event envelope (example fields; implement typed models):**

```json
{
  "schema_version": "1.0",
  "event_id": "deterministic-source-specific-id",
  "source": "sec_edgar_or_permitted_provider",
  "source_event_id": "upstream-id",
  "content_hash": "sha256",
  "source_url": "https://example.com/original",
  "published_at": "2026-01-02T14:00:00Z",
  "first_seen_at": "2026-01-02T14:00:04Z",
  "ingested_at": "2026-01-02T14:00:05Z",
  "available_at": "2026-01-02T14:00:05Z",
  "instruments": [{"symbol": "EXAMPLE", "instrument_id": "stable-id", "confidence": 0.98}],
  "source_license": "permitted_research_use",
  "payload_ref": "s3-or-minio-key",
  "trace_id": "trace-id"
}
```

Use **UTC event time** throughout. `available_at` is when a feature could actually have been used, not an assumed publication timestamp. Preserve revised documents as new versions rather than silently rewriting historical snapshots. Track ticker mappings by effective date and stable identifiers where available; account for mergers, delistings, split adjustments and trading calendars.

Implement: idempotent consumer upserts; retries with jitter; DLQ and replay; message validation; schema compatibility tests; data quality checks; late/out-of-order handling with watermark policy; per-provider ingestion lag metrics; immutable input references; tracing correlation IDs. Prefer *at-least-once delivery plus idempotent effects* over unjustified exactly-once promises.

**Point-in-time invariant:** at decision time `t`, every input must satisfy `available_at <= t`. Explicitly test this invariant in training, feature joins, dashboards, reports, and event replays.

## 5. Quantitative research and predictive modeling

### 5.1 Research tasks (keep distinct)

**Task A — financial NLP:** classify polarity and event category; identify the affected issuer and confidence; evaluate against hand-labeled examples. This is **not** itself a return prediction.

**Task B — asset-level signal:** predict **next 1-day and 5-day risk-adjusted return direction/rank** after the earliest realistically tradable timestamp, or calibrated movement probability. State the exact label, benchmark, executable decision time, and horizon.

**Task C — equity research:** summarize an issuer's current catalysts and risks using point-in-time filings, financial ratios, event sentiment, market exposures, and uncertainty. Distinguish source facts from model inference.

### 5.2 Model ladder — prove lift, don't assume it

1. **Baselines:** financial lexicon/simple aggregation, keyword/event-frequency metrics, sector momentum, and price-only models.
2. **Financial NLP baseline:** pretrained **FinBERT** sentiment classification; label quality review; temperature scaling or another validated calibration method; confidence/entropy and explicit abstention.
3. **Event representation:** deduplication clusters, novelty of information, entity relevance, event type, source credibility metadata, and time-decay features.
4. **Temporal sentiment alpha candidate:** compute trailing company and sector aggregates using only available events. One interpretable candidate:

   `S(i,t) = sum_events[j] relevance(i,j) * materiality(j) * signed_sentiment(j) * exp(-age(j,t)/tau)`

   Estimate `tau` only on training folds. Add sector/market normalization and earnings-event flags. Separate raw sentiment score from empirically learned predictive return signal.
5. **Predictive candidate:** LightGBM ranking/classification with NLP aggregates + valid lagged price/volatility/liquidity/fundamental features. Compare to a price/factor-only ablation. Consider a temporal transformer **only after** simpler models show reproducible gains.
6. **Research enhancement:** regime-conditioned sentiment (volatility/liquidity/news-intensity regimes), uncertainty-aware weighting, cross-sectional neutralization, and event-impact analysis. Test incremental value with ablations.

**Do not present these as proven alpha.** Record negative experiments and no-lift outcomes openly.

### 5.3 Leakage-resistant experiments

- Time-ordered walk-forward splits; **purging and embargo** for overlapping label horizons; an untouched final holdout.
- Point-in-time corporate actions, universe membership, fundamentals filing/acceptance timestamps, vendor revisions, and realistic source delay.
- Inference-time preprocessing must match training exactly; immutable dataset manifests, seed and code revision stored with every run.
- Compare against buy-and-hold/sector-neutral, price-only, sentiment-only, shuffled-sentiment, and lagged-news controls.
- NLP metrics: macro-F1, precision/recall, confusion matrix, calibration error, Brier score, and issuer-link accuracy.
- Forecast metrics: rank IC/Spearman, directional accuracy vs class base rates, PR-AUC as appropriate, calibration, performance by sector/cap/regime, bootstrapped confidence intervals.
- Backtest metrics: **net** cumulative return, annualized Sharpe/Sortino with uncertainty, max drawdown, turnover, transaction cost, slippage, exposure, factor beta, capacity sensitivity, and robustness over subperiods.
- Constrain turnover, participation, liquidity and position sizes. Handle no-trade and after-hours events and signal latency explicitly.
- Flag multiple-comparison/data-snooping risk; if enough trials occur, use deflated Sharpe or suitable multiple-testing diagnostics; never tune on the final holdout.

The research report must state **what was learned, what failed, and whether the evidence is strong enough to justify more work**.

## 6. Agentic equity research workflow

Use **LangGraph** with typed state and finite transitions:

```text
Request + ticker + as_of_time
       |
[Planner / scope validator]
       |
[Evidence retrieval]  -> point-in-time filings / cited news / market features
       |
[Fundamentals analyst] + [Sentiment/event analyst] + [Quant/risk analyst]
       |
[Thesis & counter-thesis synthesis]
       |
[Evidence/consistency verifier]
       |-- insufficient / contradictory -> abstain or request more evidence
       |
[Structured investment research brief]
```

**Each report must include** ticker and as-of time; a neutral business snapshot; key dated catalysts; recent filing-backed facts; positive/negative/uncertain sentiment; price/factor context; quantitative model output (if available); strongest bull and bear arguments; possible invalidation conditions; missing information; uncertainty rating; and claim-level citations.

**Rules for agents:**

- Tools are **read-only** and allowlisted (`get_filings`, `get_news`, `get_market_snapshot`, `get_features`, `get_signal`, `get_peer_comparison`). No shell execution, unrestricted external browsing, arbitrary SQL, order placement, or live broker access.
- Evidence is untrusted input: defend against prompt injection embedded in news/filings. Strip instructions from source text where appropriate and never let retrieved content modify agent permissions.
- Bound iterations, time, tokens, API spend and tool calls. Use caching, fallback providers/mock model, rate limits, and structured error outcomes.
- Validate every typed output and each claimed citation against an actual stored source identifier. **If evidence is insufficient, write “Insufficient evidence”; do not hallucinate.**
- Log model version, prompt version, evidence IDs, as-of timestamp, tool calls, latency, tokens and cost. Redact secrets and private credentials.
- No LLM-generated portfolio recommendation is allowed to silently become a model-generated executable signal.

**Agent evaluation:** curated research questions, citation-support rate, unsupported-claim rate, point-in-time correctness, factual consistency, contradiction detection, abstention quality, cost, and response latency. Use deterministic fixtures and human-reviewed examples; do not treat an LLM judge alone as ground truth.

## 7. API, dashboard, and demonstrable experience

### API outline

```http
GET  /health/live
GET  /health/ready
GET  /v1/instruments?query=...
GET  /v1/events?symbol=...&as_of=...
GET  /v1/sentiment/{symbol}?window=24h
GET  /v1/signals/{symbol}?horizon=1d
GET  /v1/research/{symbol}?as_of=...
POST /v1/research/jobs
GET  /v1/research/jobs/{job_id}
GET  /v1/backtests/{run_id}
GET  /v1/stream      # WebSocket or SSE with documented auth
```

Research jobs must be asynchronous and idempotent. Support pagination and standard error objects; include schema/model/version/time fields in responses. Apply auth, request throttling, and appropriate role separation for privileged endpoints.

### Frontend views

1. **Live market event tape:** provider, instrument, source time, first seen time, latency, novelty, polarity, uncertainty, and clickable provenance.
2. **Issuer intelligence terminal:** time-series sentiment, event clusters, market-relative performance, financial metrics, peers, and a **signal-vs-recommendation distinction**.
3. **Agent research notebook:** grounded brief, citations, trace/timeline of each graph node, bull/bear evidence, model limitations, and missing-data warnings.
4. **Quant validation workbench:** split diagrams, baselines, model ablations, rank IC, calibration, backtest charts, costs/turnover, drawdown, and reproducibility metadata.
5. **System observability:** ingest lag, message backlog, DLQ count, p50/p95/p99 processing latency, GPU/CPU utilization, inference throughput, API errors, and trace links.

Use synthetic or permissioned demo data; prominently label **simulated, delayed, or live** feeds and **research-only** outputs.

## 8. Suggested repository structure

```text
alphadots/
├── CLAUDE.md
├── README.md
├── .env.example
├── Makefile
├── pyproject.toml
├── uv.lock
├── services/
│   ├── ingestion/
│   ├── preprocessor/
│   ├── sentiment/
│   ├── market_features/
│   ├── signal_engine/
│   ├── research_agent/
│   └── api/
├── libs/
│   ├── contracts/             # Pydantic + JSON schema compatibility tests
│   ├── common/                # logging, tracing, security, retries
│   └── quant/                 # as-of joins, features, evaluation, backtesting
├── research/
│   ├── datasets/              # manifests and loaders, never proprietary data
│   ├── training/
│   ├── experiments/
│   ├── evaluation/
│   └── backtesting/
├── web/                        # Next.js / TypeScript
├── data/fixtures/              # redistributable tiny deterministic examples
├── infra/
│   ├── compose/
│   ├── helm/alphadots/
│   ├── k3d/
│   └── terraform/
│       ├── modules/
│       └── environments/dev/
├── observability/
│   ├── prometheus/
│   └── grafana/
├── .github/workflows/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── temporal_leakage/
│   ├── agent_eval/
│   ├── e2e/
│   └── performance/
├── scripts/                    # seed, replay, benchmark, dataset snapshot
└── docs/
    ├── architecture.md
    ├── data-licenses.md
    ├── threat-model.md
    ├── experiment-protocol.md
    ├── deployment-runbook.md
    ├── cost-guardrails.md
    └── adr/
```

Place code in its owning service or library. Do not create unexplained duplicate notebooks, notebooks as critical production services, global database credentials, or inter-service relative imports.

## 9. Docker, local execution, and Kubernetes

### Local Compose

Provide compose profiles: `core` (Redpanda, PostgreSQL, Redis, MinIO, API, basic services), `ml` (sentiment model), `agents` (mock or configured LLM), and `obs` (Prometheus/Grafana). Local seed/replay must operate entirely offline after dependencies/images have been installed.

Expected `Makefile` interface (implement working targets, not stubs):

```bash
make setup          # verify toolchain, install dependencies
make up             # local core platform
make seed           # load allowed test fixtures
make replay         # deterministic stream of news + bars
make smoke          # verify ingest -> score -> signal -> API
make test           # fast unit + contract + leakage tests
make test-integration
make lint
make security
make benchmark      # write measurable reproducible results
make down
make k8s-up         # k3d + Helm deployment
make k8s-smoke
make k8s-down
```

Use container health checks, explicit startup dependencies (not sleeps), graceful shutdown, bounded queues/backpressure, non-root users, multi-stage builds, `.dockerignore`, resource limits, and persistent named volumes. Do not bake `.env`, API keys or data licenses into images.

### Kubernetes

- Helm chart defines Deployment/StatefulSet as appropriate, Service, ConfigMap, Secret **references**, probes, resource requests/limits, PodDisruptionBudget when justified, and NetworkPolicy.
- One-command local `k3d` deployment uses minimal resources and pulls only explicitly named images; demonstrate scaling of stateless consumers and replay after pod restart.
- Apply HPA only with usable metrics and tested behavior; do not claim autoscaling works because an HPA file exists.
- Default to non-root, read-only root filesystem where feasible, capability drops, restricted service accounts, and deny-by-default network boundaries with required exceptions.
- Stateful infra may be single-replica locally. Never imply local k3d is equivalent to a production HA cluster.

## 10. IaC and cloud reference (AWS, optional)

Terraform modules should cover VPC/subnets, EKS (if enabled), ECR repositories, RDS PostgreSQL, S3 artifact/data buckets, IAM least-privilege roles with **GitHub Actions OIDC**, Secrets Manager references, security groups, budget tags and observability integration. Keep database/storage access private and require TLS. Use separate dev variables and remote-state/documented locking; no credentials in `.tfvars` or state exposed to users.

**Cloud cost safeguards:** local Docker/k3d is the default; `terraform plan` is the normal demonstration. An EKS/RDS environment is **not free**. Require user approval before any chargeable `terraform apply`, cluster creation, or paid data-provider use. Provide cost estimates and an explicit `terraform destroy` runbook; never run destructive infrastructure changes automatically.

## 11. CI/CD pipeline and DevSecOps policy

### Pull request gates

1. Validate Python/TypeScript formatting, static types, dependency locks, and unit tests.
2. Run event-contract compatibility, time-leakage regression tests, data validation, and deterministic mock-agent tests.
3. Run appropriate integration tests against containerized PostgreSQL/Redpanda/Redis; produce test and coverage reports (target **>=80%** on core Python libraries, excluding generated code with justification).
4. Build OCI images; scan dependencies and images with **pip-audit/Trivy**, static code with **Semgrep**, secret leakage with **Gitleaks**, Terraform/Helm manifests with **Checkov** and Helm lint/schema validation.
5. Generate a CycloneDX or SPDX **SBOM**, record image digest, and block unwaived critical/high exploitable findings according to documented policy. Exceptions require scoped justification and expiration.

### Release and deployment

- On protected main branch: version packages/images, publish to ECR or selected OCI registry, retain provenance/digests, and deploy **staging** only after checks pass.
- Production release requires explicit approval. Deploy pinned image digests, verify smoke tests and SLOs, and document Helm rollback. Do not deploy from pull requests or use long-lived AWS keys in CI.
- Use GitHub OIDC role assumption, minimal scoped tokens, dependency pinning, CODEOWNERS/branch protection guidance, and artifact retention policies.
- Treat financial text and retrieval documents as hostile inputs. Validate URLs, strip/escape markup, enforce SSRF protections, defend agent tool boundaries, and never log auth tokens or personal financial details.

Add `docs/threat-model.md` addressing prompt injection, supply-chain compromise, malicious filings/URLs, data poisoning, runaway LLM spend, replay attacks, market-data licensing, credential leakage, unauthorized API access, and cross-tenant information exposure.

## 12. Observability, benchmarks, and reproducibility

Propagate a trace ID across producer, Kafka envelope, consumer, inference, signal, agent run, and API response. Instrument logs/metrics with the correct units and dataset labels. Minimum metrics:

```text
alphadots_ingest_events_total{source,status}
alphadots_ingest_lag_seconds{source}
alphadots_kafka_consumer_lag{group,topic}
alphadots_events_dlq_total{service,reason}
alphadots_pipeline_latency_seconds{stage}   # histogram
alphadots_model_inference_seconds{model_version}  # histogram
alphadots_model_abstentions_total{reason}
alphadots_agent_tool_calls_total{tool,status}
alphadots_agent_cost_usd_total{provider}
alphadots_api_requests_total{route,status}
```

Define alert thresholds from baselines and avoid cardinality explosions (no raw ticker, article ID or user ID as metric labels). Record p50/p95/p99 end-to-end and stage latency, throughput, error rate, resource cost, and correctness under 1× and accelerated replay.

**Initial engineering targets, not claimed results:**

- A single-command local fixture **ingest → preprocess → sentiment → signal → dashboard/API** demonstration.
- Duplicate delivery and consumer restart do not produce duplicate economic events or inconsistent signals.
- Leakage invariant violations fail tests; intentionally injected post-decision documents are excluded.
- Benchmark local CPU and optional GPU separately; report p95 end-to-end latency excluding async LLM research (aspirational starting target: **<5 s on a documented local fixture workload**, revise based on measured hardware and model size).
- Store machine spec, sample count, data source/license, replay rate, model version, configuration, git SHA, seed, and complete latency distribution with each benchmark; never invent measurements.

## 13. Implementation milestones and strict exit criteria

### M0 — Foundations: first working vertical slice

- Monorepo scaffold, contracts, config, test fixtures, FastAPI health/API, Redpanda, PostgreSQL and Docker Compose.
- One synthetic headline reaches the API through typed ingest → normalization events.
- `make up`, `make seed`, `make smoke`, and `make test` pass from a clean documented environment.

### M1 — Reliable streaming and entity resolution

- SEC-compatible adapter respecting access limits plus simulated/publicly permitted input; retries, offsets, dedup, canonical instruments, as-of availability, DLQ, backfill and replay.
- Contracts and temporal leakage tests cover duplicates, late arrivals, source revisions, ambiguous issuers, and restarts.

### M2 — NLP and interpretable sentiment

- FinBERT baseline, polarity probabilities and event features, calibration evaluation, batch inference, CPU fallback, Kafka integration.
- A reproducible labeled sample and NLP evaluation report with genuine measurements and known limitations.

### M3 — Quant signal research and backtesting

- Point-in-time joins, 1-day/5-day horizons, LightGBM plus baseline ablations, purged walk-forward, embargo, cost-aware backtesting, artifact manifests and MLflow tracking.
- Present an untouched holdout report and negative findings; show performance only when empirically measured.

### M4 — Bounded agentic research + UI

- LangGraph research workflow, source retrieval, claim verification/abstention, structured report schema, API jobs, Next.js terminal, provenance drill-down.
- Agent evaluation tests with intentionally misleading articles and missing data.

### M5 — Deployment and DevSecOps

- Production-quality Docker, k3d/Helm deployment, Terraform AWS plan, GitHub Actions PR gates, OIDC release workflow, threat model, image scanning and SBOM.
- Demonstrate rescheduling/replay, basic observability dashboard, staging smoke and rollback **without requiring actual AWS spend**.

### M6 — Quant-developer performance and portfolio showcase

- Controlled benchmark (local CPU and optional GPU), backpressure/failure-injection tests, SQL/query profiling, throughput/latency optimization with before/after evidence.
- Optional C++20 acceleration only if profiling proves value. Publish a case study describing constraints, architecture tradeoffs, evaluation methodology, honest results, and reproducible demo commands.

**Implementation order is mandatory unless there is a recorded reason to change it.** Do not start with 7 empty services, flashy agent UIs, or a complex transformer before M0 is demonstrable.

## 14. Global definition of done

A feature is **done** only when all applicable items are true:

- [ ] Explicit user/research requirement, minimal design, and owning service identified.
- [ ] Typed API or event contract and schema compatibility strategy defined.
- [ ] Unit + integration + failure-mode tests written and actually run.
- [ ] Timestamps, source attribution, availability, data license, and reproducibility verified.
- [ ] No data leakage or unreviewed agent claims; report citations are retrievable.
- [ ] Reasonable error handling, retry/idempotency, auth/secret hygiene and logging.
- [ ] Metrics and traces for critical paths; performance claims linked to actual benchmark runs.
- [ ] Docker/local setup remains functional; Kubernetes/IaC updated if deployment shape changes.
- [ ] CI/security gates pass or a precise blocker is documented.
- [ ] README/ADR/runbooks explain usage, limitations, and no-trade/research-only status.

## 15. What makes AlphaDots credible to quant firms

Show **three** independently inspectable strengths, rather than claiming a production trading system:

1. **Quant researcher:** falsifiable hypothesis, point-in-time data, controlled ablations, robust walk-forward validation, net-of-cost evaluation, uncertainty, and negative-result transparency.
2. **Quant analyst:** interpretable catalyst attribution, risk/factor-aware issuer research, peer comparisons, provenance, and disciplined distinction between narratives and predictive evidence.
3. **Quant developer:** Kafka event architecture, idempotent stream processing, observable latency/backpressure, containerization, Kubernetes, Terraform, CI/CD, threat modeling, and reproducible performance testing.

### Final project deliverables

- A clean GitHub repository with setup instructions, architecture diagram, tests, infra code, and permissibly redistributable fixture dataset.
- A demo clip: replay event → sentiment/novelty → signal → agent-backed issuer brief → evidence view → evaluation panel.
- A **quant research paper-style report** with baselines, detailed methodology, leakage defenses, ablations, costs, robustness, and **actual** measured outcomes.
- An **engineering performance report** with documented machine specs, workload, p50/p95/p99, bottlenecks, fault-injection results, and before/after improvements.
- A **deployment/security case study** showing Docker, k3d/Helm, Terraform plan, GitHub CI/CD, scanning gates, and monitoring.

**Always optimize for a project an interviewer can reproduce and interrogate—not a project that merely lists impressive tools.**
