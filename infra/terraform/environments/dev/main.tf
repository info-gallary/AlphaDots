# AlphaDots AWS dev/reference environment. This file wires the six modules under
# ../../modules together. Nothing here is applied automatically: `terraform plan` is the
# normal demonstration, `terraform apply` requires the user's direct, explicit approval
# (see ./README.md for cost estimates and the destroy runbook).

locals {
  common_tags = {
    Project     = "alphadots"
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

module "vpc" {
  source = "../../modules/vpc"
  name   = var.name
  tags   = local.common_tags
}

module "ecr" {
  source = "../../modules/ecr"
  name   = var.name
  tags   = local.common_tags
}

module "s3" {
  source         = "../../modules/s3"
  name           = var.name
  account_suffix = var.account_suffix
  tags           = local.common_tags
}

module "eks" {
  source             = "../../modules/eks"
  name               = var.name
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  tags               = local.common_tags
}

module "rds" {
  source                   = "../../modules/rds"
  name                     = var.name
  vpc_id                   = module.vpc.vpc_id
  private_subnet_ids       = module.vpc.private_subnet_ids
  source_security_group_id = module.eks.node_security_group_id
  tags                     = local.common_tags
}

module "iam" {
  source              = "../../modules/iam"
  name                = var.name
  github_repository   = var.github_repository
  allowed_ref         = var.github_allowed_ref
  ecr_repository_arns = [for arn in values(module.ecr.repository_arns) : arn]
  s3_bucket_arns      = [module.s3.data_bucket_arn, module.s3.artifacts_bucket_arn]
  s3_kms_key_arn      = module.s3.kms_key_arn
  eks_cluster_arn     = module.eks.cluster_arn
  rds_secret_arn      = module.rds.credentials_secret_arn
  tags                = local.common_tags
}
