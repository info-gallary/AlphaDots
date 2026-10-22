variable "name" {
  description = "Namespace prefix for repository names, e.g. \"alphadots\" -> \"alphadots/api\"."
  type        = string
}

variable "repository_names" {
  description = "Service names, one ECR repository each (matches infra/compose service names)."
  type        = list(string)
  default = [
    "ingestion",
    "preprocessor",
    "sentiment",
    "signal-engine",
    "api",
    "research-agent",
    "web",
  ]
}

variable "untagged_expiry_days" {
  type    = number
  default = 7
}

variable "max_tagged_images" {
  type    = number
  default = 20
}

variable "tags" {
  type    = map(string)
  default = {}
}
