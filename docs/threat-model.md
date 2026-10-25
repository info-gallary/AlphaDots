# Threat model

Scope: the system as built through M0 plus the M4 research agent (ADR 0003), not the
full M5 production posture. Each section states what is mitigated today, what is not, and
which milestone closes the gap. This is an engineering document, not a compliance
artifact — it is meant to be read by someone deciding whether to trust this system with
real data, and updated every time that answer would change.

## Assets

- **Source attribution and licensing metadata** — `source_license`, `source_url` on every
  event. Losing this means publishing data without the right to.
- **The point-in-time invariant** — `available_at` on every message. A system that can be
  made to look ahead silently produces fabricated research results.
- **API credentials** — `ALPHADOTS_API_TOKEN`, `ALPHADOTS_GROQ_API_KEY`. Leaking the first
  exposes read access (no write/trade capability exists); leaking the second is a billing
  exposure at the provider.
- **The research agent's LLM budget** — bounded tokens/calls/cost per run; a bypass is a
  direct cost attack even though it is "only" a research feature.

## STRIDE by component

### Ingestion (fixture replay today; SEC/news adapters in M1)

| Threat | Mitigation | Gap |
|---|---|---|
| Tampering: a malformed or malicious source record | `FixtureReplayAdapter` quarantines anything that fails Pydantic validation to `events.dead_letter.v1`; the stream continues | A real adapter (M1) must apply the same quarantine to network responses, with a byte-size cap before parsing |
| Spoofing: a record claiming a false `published_at` | Not defended — `published_at` is accepted as a source claim; only `first_seen_at`/`available_at` (our own clocks) gate point-in-time visibility | By design; a true timestamp-forgery defense would need per-source trust scoring (unscoped) |
| Denial of service: unbounded source volume | Per-source rate limiting is named in the architecture but not implemented (no live adapter exists yet) | M1 |

### Preprocessor, sentiment, signal engine

| Threat | Mitigation | Gap |
|---|---|---|
| Tampering: HTML/script injection in headlines reaching storage or the dashboard | `clean_text()` strips `<script>`/`<style>` blocks, all markup, and Unicode control/format characters before anything is stored; React escapes all rendered text | None known for the current fixture corpus |
| Repudiation: a reprocessed message silently changing a prior result | Every output ID is derived deterministically from its inputs; `tests/temporal_leakage` and `tests/e2e` assert replays are byte-identical | None |
| Tampering: a poison record blocking a partition | Bounded retries, then quarantine (`libs/common/worker.py`); one partition today (M1 adds partition-key-safe dedup) | Dedup "first processed wins" is not yet order-independent across partitions (ADR 0001) |

### API

| Threat | Mitigation | Gap |
|---|---|---|
| Spoofing: unauthorized access to a deployed instance | Optional bearer token (`ALPHADOTS_API_TOKEN`), constant-time comparison (`hmac.compare_digest`) | No OIDC/JWT integration yet (named in the spec as "integration-ready"); no role separation — a valid token grants every read, there is nothing to write |
| Information disclosure: cardinality/label injection into metrics | `AGENT_TOOL_CALLS`, `API_REQUESTS` etc. are labelled only by route *template*, never by raw path, ticker, or user ID | None known |
| Tampering: SSRF via a crafted upstream URL | `ALPHADOTS_RESEARCH_AGENT_URL`/`ALPHADOTS_API_URL` are operator-set configuration, never derived from a request; the dashboard's own proxy (`web/src/app/api/[...path]/route.ts`) allowlists path shape and refuses redirects | A future "fetch this URL" agent tool would need its own SSRF guard (not built; out of scope — the agent has no such tool) |
| Replay: a captured bearer token reused later | No token expiry or rotation (static shared secret) | M5: short-lived tokens via OIDC |

### Research agent (ADR 0003)

| Threat | Mitigation | Gap |
|---|---|---|
| Prompt injection via retrieved evidence | A news item's full body is never sent to the model — only a headline, embedded as a quoted string inside a fenced JSON block the system prompt names as inert data (`facts.py`, `prompts.py`) | A future tool that reads full body/filing text will need the same discipline applied explicitly, not inherited for free |
| A compromised or careless model issuing a trade recommendation | `contains_recommendation_language()` scans every candidate claim and drops it, regardless of source; a report's `research_only` field is pinned `True` by the schema itself | Heuristic phrase list, not exhaustive (documented in `prompts.py`) |
| A hallucinated citation | The verifier checks every `evidence_id` against the facts the model was actually given; an abstained report is schema-rejected if it carries any citation at all | None known |
| Runaway LLM spend | Per-call token/time bounds, a bounded number of correction retries, and a reactive cost bound that refuses the next call once a run's spend crosses it (`runner.py`) | The bound is reactive (checked after each call, since cost is unknown beforehand) and per-run, not a rolling account-wide budget; add the latter before enabling a paid provider in a shared deployment |
| Supply chain: a malicious package masquerading as the configured LLM provider | `GroqAdapter` is a small, in-repo `httpx` client, not a third-party SDK — nothing to compromise upstream | N/A |
| Data poisoning: a feedback loop where agent output becomes future evidence | Impossible by construction — the agent only *reads* `/v1/events`, `/v1/signals`, `/v1/overview`; it has no write path into any upstream topic | None |
| Cross-tenant exposure | No multi-tenancy exists; single shared database per environment | Out of scope until a multi-tenant deployment is planned |

### Offline research downloads (earnings study)

| Threat | Mitigation | Gap |
|---|---|---|
| Tampering: a malicious filing or exhibit (hostile HTML, huge documents) | Exhibits are only parsed to plain text and counted; nothing is executed, rendered or sent to a model; the dashboard receives aggregates only | No byte-size cap before parsing; fine for EDGAR, required before adding untrusted sources |
| Licence breach: redistributing provider data | Raw downloads live in gitignored `var/research`; the committed JSON holds statistics over thousands of events, never a stock's prices or a release's text (a test asserts no per-stock series leave `analyse`) | Yahoo's terms do not permit automated download at all; used at the owner's explicit choice and documented in `docs/data-licenses.md`. Swap in a licensed provider before any non-personal use |
| Abuse of a provider (rate limits) | One declared identity, a shared per-provider schedule (8/s for SEC, 1/s elsewhere) across worker threads, backoff with jitter, and a persistent 429 stops the run | None known |
| Credential leakage | The SEC contact lives in `.env` (gitignored, excluded from images); Yahoo and other sources get a generic User-Agent without it | None known |

### Infrastructure (Docker, compose, Terraform, Kubernetes)

| Threat | Mitigation | Gap |
|---|---|---|
| Container escape / excess privilege | Every service image runs as a non-root user (`USER 10001:10001` in `service.Dockerfile`), with `read_only: true`, `cap_drop: ["ALL"]`, `no-new-privileges` in compose | Kubernetes manifests (`infra/helm`) carry the same `securityContext` settings; not yet validated against a Pod Security Standard |
| Secrets baked into an image | `.dockerignore` excludes `.env*`; every credential is injected via compose `environment:`/Kubernetes `Secret` references, never `COPY`'d | None known |
| Unscanned vulnerable dependencies/images | `make security` runs `pip-audit`, `pnpm audit`, and `gitleaks`; Trivy (images/filesystem), Checkov (Terraform/Helm), Semgrep (SAST) and an SBOM are wired into `make security`/CI as of this pass | Findings are not yet gated with a documented waiver policy — see "Known gaps" |
| Over-privileged cloud IAM | Terraform's IAM module is scoped per-service (ECR push for CI, RDS/S3 access for the API only) and uses GitHub Actions OIDC rather than long-lived keys | Not yet deployed or reviewed against a real AWS account; `terraform plan` only, by the cost-safeguard rule in CLAUDE.md §10 |
| CI/CD: a malicious PR exfiltrating secrets or deploying unreviewed code | PR workflow has no access to deployment secrets; OIDC role assumption is scoped to the release workflow on `main` only, gated by environment protection rules | Branch protection / required reviewers must be configured in the GitHub repository settings — not something a workflow file can enforce by itself |

## Known gaps (explicit, not hidden)

1. **Five scoped waivers, Checkov not yet gating.** Trivy fails the build on any
   HIGH/CRITICAL finding (`--exit-code 1`); `.trivyignore` is the scoped, expiring waiver
   file CLAUDE.md §11 asks for. Findings with a same-cost fix were fixed instead (for
   example public subnets no longer auto-assign IPs, S3 uses a rotating customer-managed
   key, the RDS security group has no egress). Five remain, each with a written reason and
   a 2027-04-22 review date: EKS public endpoint (AWS-0040/0041), control-plane egress
   (AWS-0104), a ConfigMap key-name false positive (KSV-0109), and writable root
   filesystems on the Postgres/Redpanda StatefulSets (KSV-0014). Semgrep (`--error`,
   `p/ci`) gates; Checkov runs informationally until there is enough finding history to
   tune a threshold.
2. **No live OIDC trust relationship exists** — the Terraform module and the release
   workflow are both written for it, but neither has been exercised against a real AWS
   account.
3. **Rate limiting, per-source backoff and poison-source quarantine** for a *live* ingestion
   adapter are designed but unimplemented, since no live adapter exists before M1.
4. **No penetration test or external review** has been performed. This document is
   author-written threat modelling, not independent verification.
5. **No live k3d cluster has been created on the primary development machine.** The
   Helm chart and `infra/k3d/cluster-config.yaml` are validated (`helm lint`,
   `helm template`), but `k3d cluster create` depends on pulling
   `ghcr.io/k3d-io/k3d-tools`, and GHCR blob pulls are unreachable from that machine —
   see `docs/deployment-runbook.md`'s k3d caveat. The Kubernetes posture described
   above (NetworkPolicy, securityContext, probes) has been reviewed in the chart's
   source and rendered output, not observed running.

## Reporting

This is a research/portfolio project with no production deployment. There is no public
bug bounty; file an issue in the repository for anything found.
