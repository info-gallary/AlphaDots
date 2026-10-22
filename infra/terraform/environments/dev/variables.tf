variable "name" {
  description = "Resource name prefix for this environment."
  type        = string
  default     = "alphadots-dev"
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "account_suffix" {
  description = "Suffix to make S3 bucket names globally unique, e.g. the AWS account id. No default: must be supplied (see terraform.tfvars.example)."
  type        = string
}

variable "github_repository" {
  description = "\"org/repo\" that the GitHub Actions OIDC role trusts. No default: must be supplied."
  type        = string
}

variable "github_allowed_ref" {
  description = "Optional: restrict the OIDC role to one ref, e.g. \"refs/heads/main\". Null allows any ref in the repo."
  type        = string
  default     = null
}
