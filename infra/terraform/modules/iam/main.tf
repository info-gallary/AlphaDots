# GitHub Actions OIDC trust, scoped to one repository and (optionally) one ref, plus a
# role with least-privilege access to exactly the resources this project's CI/CD needs:
# push to this project's ECR repos, read/write the two S3 buckets, describe the EKS
# cluster, and read the RDS master-credentials secret. No `Resource: "*"` anywhere.

data "tls_certificate" "github_oidc" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github_oidc.certificates[0].sha1_fingerprint]
  tags            = var.tags
}

locals {
  # e.g. "repo:info-gallary/AlphaDots:ref:refs/heads/main" when allowed_ref is set,
  # otherwise "repo:info-gallary/AlphaDots:*" (any branch/PR/tag in the repo).
  subject = var.allowed_ref != null ? "repo:${var.github_repository}:ref:${var.allowed_ref}" : "repo:${var.github_repository}:*"
}

data "aws_iam_policy_document" "trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.subject]
    }
  }
}

resource "aws_iam_role" "ci" {
  name                 = "${var.name}-github-actions"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
  tags                 = var.tags
}

data "aws_iam_policy_document" "ci_permissions" {
  statement {
    sid       = "EcrAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # GetAuthorizationToken has no resource-level permissions in AWS
  }

  statement {
    sid    = "EcrPush"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:PutImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeRepositories",
      "ecr:DescribeImages",
      "ecr:ListImages",
    ]
    resources = var.ecr_repository_arns
  }

  statement {
    sid       = "S3ReadWrite"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"]
    resources = concat(var.s3_bucket_arns, [for arn in var.s3_bucket_arns : "${arn}/*"])
  }

  # The buckets are encrypted with a customer-managed KMS key (not the AWS-managed
  # default), so S3 permissions alone are not enough - every read/write also needs the
  # matching KMS grant, or every S3ReadWrite call above fails at decrypt/encrypt time.
  statement {
    sid       = "S3KmsForBuckets"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:GenerateDataKey*"]
    resources = [var.s3_kms_key_arn]
  }

  statement {
    sid       = "EksDescribe"
    effect    = "Allow"
    actions   = ["eks:DescribeCluster"]
    resources = [var.eks_cluster_arn]
  }

  statement {
    sid       = "RdsSecretRead"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.rds_secret_arn]
  }
}

resource "aws_iam_role_policy" "ci" {
  name   = "${var.name}-github-actions-permissions"
  role   = aws_iam_role.ci.id
  policy = data.aws_iam_policy_document.ci_permissions.json
}
