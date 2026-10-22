output "data_bucket_name" {
  value = aws_s3_bucket.data.id
}

output "data_bucket_arn" {
  value = aws_s3_bucket.data.arn
}

output "artifacts_bucket_name" {
  value = aws_s3_bucket.artifacts.id
}

output "artifacts_bucket_arn" {
  value = aws_s3_bucket.artifacts.arn
}

output "kms_key_arn" {
  description = "The CMK both buckets are encrypted with. Any principal reading/writing objects needs kms:Decrypt/kms:GenerateDataKey* on this ARN, in addition to its S3 permissions."
  value       = aws_kms_key.s3.arn
}
