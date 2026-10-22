variable "name" {
  description = "Name prefix, e.g. \"alphadots-dev\"."
  type        = string
}

variable "account_suffix" {
  description = "Suffix to make bucket names globally unique (e.g. the AWS account id). Required because S3 bucket names are global."
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
