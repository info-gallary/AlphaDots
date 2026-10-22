variable "name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "source_security_group_id" {
  description = "Security group allowed to reach PostgreSQL on 5432 (the EKS node security group)."
  type        = string
}

variable "database_name" {
  type    = string
  default = "alphadots"
}

variable "master_username" {
  type    = string
  default = "alphadots"
}

variable "engine_version" {
  type    = string
  default = "17.4"
}

variable "instance_class" {
  description = "Smallest viable burstable class for a dev/reference environment."
  type        = string
  default     = "db.t4g.micro"
}

variable "allocated_storage_gb" {
  type    = number
  default = 20
}

variable "max_allocated_storage_gb" {
  description = "Storage autoscaling ceiling."
  type        = number
  default     = 100
}

variable "backup_retention_days" {
  type    = number
  default = 3
}

variable "multi_az" {
  description = "Off by default: a dev/reference environment does not need a standby replica at 2x the cost."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Off by default so `terraform destroy` can tear this down cleanly without a manual override."
  type        = bool
  default     = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
