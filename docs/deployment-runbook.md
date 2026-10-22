# Deployment runbook

Three deployment shapes exist today, in order of how real they are: local Docker
Compose (works, no credentials needed, exercised repeatedly this session), local
k3d/Helm (chart and config validated — `helm lint`/`helm template` both pass, including
the conditional PDB/HPA paths — but **not yet exercised against a live cluster on this
development machine**; see the caveat below), and AWS via Terraform/EKS (written and
`plan`/`validate`-checked, never `apply`-ed — see `docs/cost-guardrails.md`). This
runbook covers operating the first two and the deploy/rollback mechanics the third is
built for.

**k3d caveat, specific to this machine, not to the chart:** `k3d cluster create` pulls
`ghcr.io/k3d-io/k3d-tools` for an internal "tools node" before it creates anything else,
on every invocation. GHCR blob pulls are unreachable from this development machine (a
pre-existing, reproducible network restriction — see memory `dev-environment-quirks` and
`infra/k3d/cluster-config.yaml`'s header comment), so that pull retries indefinitely and
cluster creation never completes here. `scripts/tasks.py`'s `k8s_up`/`k8s_smoke`/
`k8s_down` are implemented and were exercised up to that point (images build, the
cluster-create command is correctly formed, cleanup works); the remaining steps —
importing images, `helm upgrade --install`, and the ingestion-Job smoke check — are
implemented but unverified end-to-end on this machine pending ghcr.io access. A machine
that can reach ghcr.io should see this complete normally.

## Local Docker Compose

```bash
uv run python -m scripts.tasks up      # core: Redpanda, Postgres, pipeline services, API
uv run python -m scripts.tasks seed    # load the curated fixture
uv run python -m scripts.tasks agents  # + the research agent (mock, unless ALPHADOTS_GROQ_API_KEY is set)
uv run python -m scripts.tasks ui      # + the Next.js dashboard, http://localhost:13000
uv run python -m scripts.tasks obs     # + Prometheus/Grafana, http://localhost:13001
uv run python -m scripts.tasks smoke   # verify ingest -> score -> signal -> API
```

Tear down with `uv run python -m scripts.tasks down` (add `-- --volumes` to also delete
data). Logs: `docker compose -f infra/compose/docker-compose.yml logs -f <service>`.

## Local Kubernetes (k3d + Helm)

```bash
uv run python -m scripts.tasks k8s-up      # build images, create/reuse the k3d cluster, helm upgrade --install
uv run python -m scripts.tasks k8s-smoke   # unsuspend the ingestion Job, verify ingest -> score -> signal -> API
uv run python -m scripts.tasks k8s-down    # helm uninstall, delete the k3d cluster
```

`k8s-up` does, in order (see `scripts/tasks.py`'s `k8s_up`/`_k3d_cluster_exists`): builds
all seven service images locally (`alphadots/<service>:dev`, matching
`infra/helm/alphadots/values.yaml`'s default `image.tag`/`web.image.tag`), creates the
k3d cluster from `infra/k3d/cluster-config.yaml` if one named `alphadots-dev` doesn't
already exist (idempotent — safe to re-run), `k3d image import`s all seven images into
it (no registry needed locally), then `helm upgrade --install alphadots
infra/helm/alphadots --wait`.

The k3d config maps host ports 30080 and 30300 directly onto the cluster's NodePort
Services (`api.nodePort`/`web.nodePort` in values.yaml), so the API and dashboard are
reachable at `http://localhost:30080` and `http://localhost:30300` with no
`kubectl port-forward` needed.

Rolling update after a code change: `uv run python -m scripts.tasks k8s-up` again — it
rebuilds every image, re-imports them, and `helm upgrade` picks up the new image content
under the same `:dev` tag (`imagePullPolicy: IfNotPresent` is safe here only because
`k3d image import` replaces the tag's content in containerd's local store directly; a
real registry deployment must use a unique tag/digest per build instead, as the AWS
shape below does).

Rollback to the previous release:

```bash
helm history alphadots          # find the revision to go back to
helm rollback alphadots <REVISION>
```

Tear down: `uv run python -m scripts.tasks k8s-down` (equivalently
`helm uninstall alphadots && k3d cluster delete alphadots-dev`).

## AWS (Terraform + EKS) — staging/production shape

This is the shape the CI/CD release workflow (`.github/workflows/release.yml`) deploys
to once a real AWS account is wired up. Today it is **not wired up**
(`vars.AWS_DEPLOY_ROLE_ARN` is unset), so `deploy-staging` and `deploy-production` no-op
on every run. Turning it on:

1. Create the Terraform state backend resources (S3 bucket + DynamoDB lock table) by
   hand, once, outside Terraform itself — `infra/terraform/environments/dev/backend.tf`
   intentionally does not create its own backend (chicken-and-egg).
2. `terraform -chdir=infra/terraform/environments/dev init`, then `plan`, then — only
   with direct human approval of that specific plan — `apply`. See
   `docs/cost-guardrails.md` for what this costs and the destroy procedure.
3. Set the `AWS_DEPLOY_ROLE_ARN` repository variable to the IAM role ARN the `iam`
   Terraform module just created (`terraform output github_actions_role_arn`).
4. Configure the `staging` and `production` GitHub Environments in repository settings;
   add required reviewers to `production` so the workflow's approval gate is real, not
   just a name in a YAML file.
5. From here, every push to `main` that passes the PR gate builds, scans, and pushes
   images, deploys to staging automatically, and deploys to production only after a
   human approves the `production` environment's pending deployment.

Rollback in this shape is the same `helm rollback alphadots <REVISION>` command as
local k3d, run against the EKS cluster's kubeconfig
(`aws eks update-kubeconfig --name <cluster>`) instead of k3d's. The release workflow
prints the exact rollback command for the revision it just deployed as its last step —
it never rolls back automatically.

## Smoke-testing any of the three

The same check works everywhere the API is reachable:

```bash
curl -sf "$API_URL/health/ready"
uv run python -m scripts.smoke --base-url "$API_URL"
```

`$API_URL` is `http://localhost:18000` for Compose, `http://localhost:30080` for k3d (via
the NodePort mapping in `infra/k3d/cluster-config.yaml` — no port-forward needed), or the
real staging/production URL once one exists.
