# AlphaDots AWS dev/reference environment

This is the optional, cloud-provider half of the project's infrastructure requirement.
**Local Docker/k3d is the default development path** (see the repository root `README.md`
and `infra/k3d/`); nothing here is required to run AlphaDots. This exists to show a real,
`terraform validate`-clean AWS reference deployment: VPC, EKS, ECR, RDS, S3, and a
GitHub-Actions-OIDC IAM role, wired together in `main.tf` from the modules under
`../../modules/`.

## What this deploys

| Resource | Module | Notes |
|---|---|---|
| VPC, 2 public + 2 private subnets, 1 NAT gateway | `vpc` | Single NAT (cost tradeoff, see module comment) |
| EKS cluster + 1 managed node group (2x `t3.medium`) | `eks` | Public+private API endpoint, its own IRSA OIDC provider |
| 7 ECR repositories (one per service image) | `ecr` | Scan-on-push, lifecycle-expires untagged/old images |
| RDS PostgreSQL `db.t4g.micro`, single-AZ | `rds` | Private only; master credentials in Secrets Manager, never a plain output |
| 2 S3 buckets (`data`, `artifacts`) | `s3` | Versioned, SSE-KMS, public access fully blocked, lifecycle tiers to IA/Glacier |
| GitHub Actions OIDC provider + least-privilege role | `iam` | Scoped to this repo's `sub` claim; no long-lived AWS keys in CI |

## Cost — list-price estimates, never a measured bill

These are **rough, undiscounted us-east-1 list-price estimates** for keeping this
environment running continuously for a month. They are not a quote, do not include data
transfer/request costs, and have never been reconciled against an actual AWS bill,
consistent with this project's rule against fabricating measured results.

| Item | Est. monthly cost |
|---|---|
| EKS control plane | ~$73 |
| 2x `t3.medium` on-demand nodes | ~$60 |
| 1 NAT gateway (base price, excl. data processed) | ~$32 + $0.045/GB |
| RDS `db.t4g.micro`, single-AZ, 20GB gp3 | ~$15 |
| S3 (data+artifacts, usage-dependent) | a few dollars at fixture-data scale |
| ECR storage (usage-dependent) | a few dollars |
| **Total, approximate** | **~$180-200/month** while running |

Nothing here auto-scales to zero; this is an always-on reference stack, not a
scale-to-zero demo. Destroy it when you're not using it (see below).

## Safety rules (from `CLAUDE.md` §10 — binding, not optional)

- **`terraform plan` is the normal demonstration.** It requires real AWS credentials
  (`aws configure` / environment variables) to run but creates nothing.
- **`terraform apply` requires the user's explicit, direct approval for this specific
  run.** Neither Claude nor any CI workflow runs `apply` automatically against this
  environment. The release workflow's AWS deploy steps are gated behind a repository
  variable (`AWS_DEPLOY_ROLE_ARN`) that is empty by default and must be set on purpose
  after this environment has actually been applied once by hand.
- No credentials are ever committed. `terraform.tfvars` (not the `.example` file) is
  git-ignored.

## Validating without AWS credentials

```bash
terraform fmt -recursive -check ../../
terraform -chdir=. init -backend=false   # skips the S3 backend; no bucket exists yet
terraform -chdir=. validate
```

`init -backend=false` and `validate` check syntax, types, and module wiring without
touching AWS at all. `plan` additionally needs credentials, because it refreshes data
sources like `aws_availability_zones`.

## Bootstrapping the backend (one-time, by hand, before the first real `init`)

```bash
aws s3api create-bucket --bucket alphadots-dev-tfstate --region us-east-1 \
  --create-bucket-configuration LocationConstraint=us-east-1
aws s3api put-bucket-versioning --bucket alphadots-dev-tfstate \
  --versioning-configuration Status=Enabled
aws dynamodb create-table --table-name alphadots-dev-tfstate-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
```

## Destroy runbook

```bash
# 1. Empty the versioned S3 buckets first - `destroy` does not force-empty a bucket
#    that still holds object versions, and will fail on it otherwise.
aws s3api list-object-versions --bucket <data-bucket-name> \
  --output json --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' \
  | aws s3api delete-objects --bucket <data-bucket-name> --delete file:///dev/stdin
# repeat for the artifacts bucket, and for DeleteMarkers the same way if present

# 2. Destroy everything this configuration manages.
terraform destroy

# 3. Confirm nothing billable is left.
aws eks list-clusters --region us-east-1
aws rds describe-db-instances --region us-east-1
aws ec2 describe-nat-gateways --region us-east-1 --filter Name=state,Values=available
```

`terraform destroy` is itself a direct, explicit action — run it only when you mean to,
and only after `apply` was run with the same direct approval in the first place.
