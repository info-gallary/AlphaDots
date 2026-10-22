variable "name" {
  type = string
}

variable "github_repository" {
  description = "\"org/repo\", e.g. \"info-gallary/AlphaDots\". No default: must be supplied explicitly."
  type        = string
  validation {
    condition     = can(regex("^[^/]+/[^/]+$", var.github_repository))
    error_message = "github_repository must be in \"org/repo\" form."
  }
}

variable "allowed_ref" {
  description = "Restrict assumption to one ref, e.g. \"refs/heads/main\". Null allows any ref/PR/tag in the repo (looser; fine for a dev environment, tighten for a real release role)."
  type        = string
  default     = null
}

variable "ecr_repository_arns" {
  type = list(string)
}

variable "s3_bucket_arns" {
  type = list(string)
}

variable "s3_kms_key_arn" {
  description = "The CMK the s3 module's buckets are encrypted with (its kms_key_arn output)."
  type        = string
}

variable "eks_cluster_arn" {
  type = string
}

variable "rds_secret_arn" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
