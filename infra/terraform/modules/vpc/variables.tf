variable "name" {
  description = "Name prefix for VPC resources, e.g. \"alphadots-dev\"."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC. Subnets are carved out of it with cidrsubnet(..., 4, n)."
  type        = string
  default     = "10.42.0.0/16"
}

variable "tags" {
  description = "Tags applied to every resource in this module (merge in cost-allocation tags here)."
  type        = map(string)
  default     = {}
}
