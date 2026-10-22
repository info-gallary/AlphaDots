# Two buckets: `data` for immutable raw/analytical event payloads (Parquet, the
# payload_ref targets in the event envelope) and `artifacts` for MLflow run artifacts and
# model registry blobs. Both are private, versioned, encrypted, and block all public
# access; nothing here is ever reachable without an IAM principal.

# A customer-managed key, not the AWS-managed default `aws/s3` key: the latter can't be
# rotated, scoped, or audited independently of every other AWS-managed-key use in the
# account (Trivy AWS-0132). Rotation is enabled so the key material itself ages out on
# a schedule without any application-level change.
resource "aws_kms_key" "s3" {
  description         = "CMK for ${var.name} S3 buckets (data, artifacts)"
  enable_key_rotation = true
  tags                = var.tags
}

resource "aws_kms_alias" "s3" {
  name          = "alias/${var.name}-s3"
  target_key_id = aws_kms_key.s3.key_id
}

resource "aws_s3_bucket" "data" {
  bucket = "${var.name}-data-${var.account_suffix}"
  tags   = var.tags
}

resource "aws_s3_bucket" "artifacts" {
  bucket = "${var.name}-artifacts-${var.account_suffix}"
  tags   = var.tags
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.s3.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.s3.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Raw payloads are write-once/read-many and cheap to keep, but age out of Standard into
# cheaper tiers since point-in-time research reads old data far less often than new data.
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    id     = "age-out-raw-payloads"
    status = "Enabled"
    filter {}
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 90
      storage_class = "GLACIER"
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "expire-noncurrent-artifact-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 14
    }
  }
}
