# AlphaDots

A point-in-time-correct research platform that turns financial news into sentiment
features and research signals, with every number traceable to its source and to the
moment it became knowable.

![Market overview](docs/images/overview.png)

## What works today

- **A typed, versioned event pipeline on Redpanda**: ingestion → preprocessing (markup
  stripping, exact dedup, effective-dated issuer resolution) → lexicon sentiment →
  decayed-sentiment signal → FastAPI read model.
- **Point-in-time correctness by construction.** Every read takes `as_of`. A score is
  hidden until its own `available_at`, and future data injected into tests changes nothing.
- **At-least-once delivery with idempotent effects.** Replays, redeliveries and restarts
  leave state byte-identical, and processing out of order gives the same signals.
- **A bounded LangGraph research agent.** It writes issuer briefs where every claim cites
  a stored item, and abstains when evidence is thin (it does for every NSE issuer, which
  has no news). A second graph monitors the pipeline. Runs on Groq with a key, or on a
  deterministic mock with none. See [ADR 0003](docs/adr/0003-bounded-research-agent.md).
- **A leakage-resistant backtest of the signal**, run where the right answer is known: a
  synthetic market with a planted effect and the same market with none. Expanding
  walk-forward with purge and embargo, `τ` chosen on training folds only, an untouched
  holdout, costs, block-bootstrap Sharpe intervals, a deflated Sharpe ratio, and controls
  (shuffled, lagged, price momentum, and a deliberate look-ahead bug). The method finds the
  planted effect and stays silent on the null; the look-ahead bug turns "no effect" into a
  Sharpe of +4.7. See the [study](docs/research/simulation-study.md).
- **A research dashboard**: a market overview (signal leaderboard, tone mix, daily news
  flow, issuer × day heatmap, strongest headlines), the event tape with provenance
  drill-down, issuer timelines, the research notebook, the backtest lab, and pipeline
  monitoring. An as-of slider shows exactly what the system knew at any moment, and every
  chart has a table view.
- **Deployable to Vercel as is.** With no backend attached, the dashboard serves the real
  pipeline's output, plus agent briefs recorded at export, from a parity-tested snapshot.
- **Deployment and DevSecOps**: multi-stage non-root images, Docker Compose profiles, a
  Helm chart and k3d config, Terraform for AWS (VPC, EKS, ECR, RDS, S3, OIDC IAM),
  GitHub Actions PR and release workflows, Prometheus and Grafana, six security scanners,
  an SBOM, and a [threat model](docs/threat-model.md).

![Research notebook](docs/images/research.png)

![Backtest lab](docs/images/backtest.png)

## Quick start

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Node 22 with pnpm, and
Docker for the full stack. Every `make` target also runs without make as
`uv run python -m scripts.tasks <target>`, which is the easier route on Windows.

**1. Dashboard only (snapshot mode, nothing else needed)**

```bash
cd web && pnpm install && pnpm dev        # http://localhost:3000
```

**2. Whole pipeline in one process, no Docker**

```bash
uv sync
make dev                                  # API on http://localhost:18000/docs
# in another terminal:
cd web && ALPHADOTS_API_URL=http://localhost:18000 pnpm dev
```

`make dev ARGS="--speed 20000"` paces the replay so the tape fills in live.

**3. The real stack: Redpanda, PostgreSQL and one container per service**

`make demo` does all of it in one go: starts every profile, seeds, replays the news at a
watchable pace, runs the research agent for every issuer and prints the URLs, so every
dashboard (the web app on :13000, Grafana on :13001) has data. Step by step:

```bash
make up          # builds images, starts everything, waits until healthy
make seed        # curated fixture (idempotent; run it twice and nothing changes)
make smoke       # 13 end-to-end checks against the running API
make replay      # bulk fixture; add SPEED=20000 for a paced live tape
make agents      # research agent (Groq if ALPHADOTS_GROQ_API_KEY is set, else mock)
make agent-smoke # 9 checks: jobs, idempotency, citations, abstention, monitoring
make ui          # dashboard container on http://localhost:13000
make obs         # Prometheus on :19090, Grafana on http://localhost:13001
make down        # ARGS=--volumes also deletes data
```

Grafana opens the pipeline dashboard without a login (anonymous, read-only, bound to
127.0.0.1). To edit, sign in as `admin` with `ALPHADOTS_GRAFANA_PASSWORD` (default
`admin`). Run-once jobs such as ingestion push their metrics to a Prometheus Pushgateway,
so the ingest panels stay filled after the job exits.

**4. The research study**

```bash
make research    # a few seconds; rewrites the backtest results and the report
```

Kubernetes: `make k8s-up`, `make k8s-smoke`, `make k8s-down` (k3d and Helm). Steps,
rollback and the AWS path are in [`docs/deployment-runbook.md`](docs/deployment-runbook.md);
what AWS would cost, and why nothing applies it automatically, is in
[`docs/cost-guardrails.md`](docs/cost-guardrails.md).

## Deploy the dashboard to Vercel

Import the repository in Vercel, set **Root Directory** to `web`, and deploy. No
environment variables are needed: it serves the bundled snapshot, including a recorded
research brief for every issuer, and shows a **Snapshot** chip. Set `ALPHADOTS_API_URL`
(and optionally `ALPHADOTS_API_TOKEN`) to attach a running API instead, which also
enables generating new briefs.

Vercel cannot run Kafka consumers or databases, so the pipeline itself is not deployed
there. [`docs/deployment-vercel.md`](docs/deployment-vercel.md) covers both modes, the
CLI route, security properties and caching.

## Tests and checks

| Command | What it covers |
|---|---|
| `make test` | Python unit, contract, point-in-time leakage and end-to-end tests (in-memory bus, no Docker), plus dashboard parity against the real API |
| `make test-integration` | The same pipeline on throwaway Redpanda and PostgreSQL containers: offset resume after restart, full redelivery, Kafka/Postgres output identical to the in-memory run |
| `make lint` | ruff, strict mypy, schema-snapshot and fixture drift, snapshot drift, research-results drift, ESLint, `tsc` |
| `make security` | pip-audit, `pnpm audit`, Gitleaks, Semgrep, Trivy (gating); Checkov (reporting); CycloneDX SBOM |
| `make web-build` | The production dashboard build Vercel runs |

CI runs the same gates on every pull request (`.github/workflows/pr.yml`), plus a Trivy
scan of every built image and a coverage floor of 80% (currently about 95%).

Tests worth reading first:
[`tests/temporal_leakage`](tests/temporal_leakage/test_point_in_time.py),
[`tests/research`](tests/research/test_study.py),
[`tests/e2e`](tests/e2e/test_pipeline.py) and
[`web/test/parity.test.ts`](web/test/parity.test.ts).

## Repository layout

```text
libs/contracts     versioned Pydantic messages, topic registry, schema compatibility
libs/common        bus (Kafka + in-memory), worker loop, config, logging, metrics, DB
libs/quant         decayed-sentiment aggregate with the point-in-time guard
libs/llm           bounded LLM adapter: Groq over plain httpx, deterministic mock for CI
services/          ingestion · preprocessor · sentiment · signal_engine · api · research_agent
research/          offline study: market simulation, walk-forward, metrics, deflated Sharpe
web/               Next.js dashboard (live proxy or bundled snapshot)
data/fixtures/     synthetic news (CC0) plus the instrument master, hash-pinned by MANIFEST.json
infra/compose      Docker Compose stack and throwaway integration stack
infra/helm, k3d    Helm chart and local cluster config
infra/terraform    AWS modules and the dev environment (validated, never applied)
observability/     Prometheus scrape config, Grafana datasource and dashboard
.github/workflows  PR verification and release workflows
scripts/           task runner, fixture generator, snapshot exporter, smoke checks
tests/             unit · contract · temporal_leakage · research · e2e · integration
docs/              architecture, research report, threat model, runbooks, ADRs
```

## Documentation

- [Architecture](docs/architecture.md): event flow, contracts, the availability model,
  delivery guarantees
- [Simulation study](docs/research/simulation-study.md): method, results, what failed,
  and whether it justifies more work
- [Data and licences](docs/data-licenses.md): what the fixtures contain and why each seed
  line exists
- [Deploying to Vercel](docs/deployment-vercel.md) and the
  [deployment runbook](docs/deployment-runbook.md) (Compose, k3d/Helm, AWS)
- [Threat model](docs/threat-model.md) and [cost guardrails](docs/cost-guardrails.md)
- ADRs: [0001 M0 scope](docs/adr/0001-m0-vertical-slice.md),
  [0002 dashboard snapshot mode](docs/adr/0002-dashboard-snapshot-mode.md),
  [0003 bounded research agent](docs/adr/0003-bounded-research-agent.md)

## Known limitations

- The sentiment model is a small hand-written lexicon. It is easy to fool; one fixture
  shows a prompt-injection sentence raising its score.
- The backtest runs on a synthetic market. It shows the evaluation can tell a real
  effect from none; it says nothing about real returns. Even in the planted world the
  daily-rebalanced strategy is not robustly profitable after 10 bps costs (net Sharpe 95%
  interval −0.64 to +3.13, deflated Sharpe probability 0.54). The live signal engine still
  uses a configured `τ`.
- Dedup is exact-match after normalisation. "First processed wins" is not yet
  order-independent (M1 watermark policy).
- NSE India issuers have no news source yet, so they carry no events or signals and the
  research agent abstains for them. Attaching one needs a licensed feed, not scraping.
- The Helm chart is linted and templated, but `k3d cluster create` could not run on the
  development machine (it cannot reach ghcr.io for k3d's helper image). The AWS
  Terraform passes `validate` and has never been applied.
- Not yet built: the SEC adapter (M1), FinBERT (M2), real market data, LightGBM and
  MLflow (M3), and benchmarks (M6). The matching `make` targets fail with the milestone that owns them
  instead of pretending.
