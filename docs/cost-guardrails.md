# Cost guardrails

Two different kinds of spend can run away in this system: cloud infrastructure and LLM
API usage. Both are bounded here, and both bounds are meant to be read before either is
turned on for real, not discovered after a bill arrives.

## Cloud infrastructure (AWS, `infra/terraform/`)

**Nothing in this repository can create a billable AWS resource by itself.**

- `terraform plan` is the only command CI or a local run ever executes against the
  `infra/terraform/environments/dev` stack. `terraform apply` requires a human to run it
  by hand, after reading the plan, having been given direct approval to spend money. No
  script, workflow, or agent in this repository runs `apply`.
- The GitHub Actions release workflow's AWS deployment jobs (`deploy-staging`,
  `deploy-production` in `.github/workflows/release.yml`) are gated on a repository
  variable (`vars.AWS_DEPLOY_ROLE_ARN`) that is unset by default. Unset, those jobs no-op
  rather than fail — the pipeline is demonstrably wired for a real account without
  requiring one to exist.
- Production deploys additionally require a GitHub Environment approval
  (`environment: production`), configured with required reviewers in repository
  settings — a second human gate independent of the AWS variable above.

### Rough list-price estimate, `environments/dev` stack, if ever applied

These are **list-price estimates from public AWS pricing, not a measured bill.** Actual
cost depends on region, usage, and pricing changes since this was written.

| Resource | Est. monthly cost (us-east-1) |
|---|---|
| EKS control plane | ~$73 |
| 2x `t3.medium` node group (on-demand) | ~$60 |
| 1x NAT gateway (hourly + data processing) | ~$33 + data |
| RDS `db.t4g.micro`, single-AZ | ~$12 |
| S3 (data + artifacts buckets) | usage-dependent, likely single-digit $ at demo scale |
| ECR storage | usage-dependent, likely <$1 at this image count |
| Secrets Manager (1 secret) | ~$0.40 |
| **Total, idle** | **roughly $180-190/month** |

A single NAT gateway (not one per AZ) and a single-AZ RDS instance are deliberate
cost-vs-availability tradeoffs for a reference/demo environment — see
`infra/terraform/environments/dev/README.md`. Neither is an acceptable choice for an
actual production deployment.

### Destroy runbook

```bash
terraform -chdir=infra/terraform/environments/dev destroy
```

Run only after confirming the plan it prints matches what you expect to delete. S3
bucket versioning means `destroy` will fail to remove a non-empty bucket until every
object version is deleted first:

```bash
aws s3api delete-objects --bucket <bucket> --delete "$(aws s3api list-object-versions \
  --bucket <bucket> --output json --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}')"
```

repeated for both the data and artifacts buckets before `destroy`, or delete the buckets
by hand in the console if the stack is being abandoned entirely.

## LLM spend (research agent, `services/research_agent/`)

Every agent run, mock or live, is bounded by `.env` values applied in
`services/research_agent/src/alphadots_research_agent/runner.py`:

| Variable | Default | What it bounds |
|---|---|---|
| `ALPHADOTS_AGENT_MAX_TOOL_CALLS` | 8 | Read-only tool calls per run |
| `ALPHADOTS_AGENT_MAX_TOKENS` | 900 | Tokens per model call |
| `ALPHADOTS_AGENT_TIMEOUT_SECONDS` | 25 | Wall-clock time per run |
| `ALPHADOTS_AGENT_MAX_COST_USD` | 0.02 | Spend per run (see below) |

The cost bound is **reactive, not predictive**: actual token cost from a provider is
only known after a call returns, so the runner checks cumulative spend after each call
and refuses to start the next one once the bound is crossed, rather than estimating cost
upfront and refusing before the first call. A single call can still exceed the bound
slightly before the check fires; this is a per-run cap on a single research request, not
a rolling account-wide budget. A shared/production deployment calling a paid provider
should add a separate account-level spend tracker before relying on this per-run bound
alone — tracked as a known gap in `docs/threat-model.md`.

Cost is only ever reported as a real number when both
`ALPHADOTS_GROQ_PRICE_INPUT_PER_M_USD` and `ALPHADOTS_GROQ_PRICE_OUTPUT_PER_M_USD` are
set from the provider's current published rate; left unset, `cost_usd` in every report is
`0.0` and is explicitly not a claim that the call was free. The deterministic mock
adapter (default when `ALPHADOTS_GROQ_API_KEY` is unset) never calls a network API and
never costs anything.
