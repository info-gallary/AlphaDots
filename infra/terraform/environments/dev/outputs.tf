output "vpc_id" {
  value = module.vpc.vpc_id
}

output "eks_cluster_name" {
  value = module.eks.cluster_name
}

output "eks_cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "ecr_repository_urls" {
  value = module.ecr.repository_urls
}

output "s3_data_bucket_name" {
  value = module.s3.data_bucket_name
}

output "s3_artifacts_bucket_name" {
  value = module.s3.artifacts_bucket_name
}

output "rds_endpoint" {
  value = module.rds.endpoint
}

output "rds_credentials_secret_arn" {
  value = module.rds.credentials_secret_arn
}

output "github_actions_role_arn" {
  description = "Set this as the AWS_DEPLOY_ROLE_ARN repository variable in GitHub Actions."
  value       = module.iam.role_arn
}

output "kubeconfig_command" {
  value = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region}"
}
