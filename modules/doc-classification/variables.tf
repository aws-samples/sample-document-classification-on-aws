# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

variable "name" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.name))
    error_message = "name must be 3-21 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "region" {
  type = string
}

variable "release_version" {
  type    = string
  default = "0.1.0"
}

variable "artifact_dir" {
  type    = string
  default = null
}

variable "vpc_mode" {
  type    = string
  default = "none"
  validation {
    condition     = contains(["none", "byo"], var.vpc_mode)
    error_message = "vpc_mode must be none or byo."
  }
}

variable "vpc_id" {
  type    = string
  default = null
}

variable "subnet_ids" {
  type    = list(string)
  default = []
}

variable "route_table_ids" {
  type    = list(string)
  default = []
}

variable "enable_tier3" {
  type    = bool
  default = false
}

variable "tier3_endpoints" {
  type = map(object({
    image_uri         = string
    model_data_url    = string
    memory_size_in_mb = number
    max_concurrency   = number
  }))
  default = {}
  validation {
    condition = alltrue([
      for k, v in var.tier3_endpoints :
      contains([1024, 2048, 3072, 4096, 5120, 6144], v.memory_size_in_mb) && v.max_concurrency >= 1 && v.max_concurrency <= 200 && can(regex("^[a-z][a-z0-9-]{1,30}$", k))
    ])
    error_message = "each tier3 endpoint needs memory_size_in_mb in 1024..6144 (steps of 1024), max_concurrency in 1..200, and a namespace key."
  }
}

variable "kms_key_arn" {
  type    = string
  default = null
}

variable "alarm_sns_topic_arn" {
  type    = string
  default = null
}

variable "log_retention_days" {
  type    = number
  default = 365
}

variable "waf_rate_limit" {
  type    = number
  default = 2000
}

variable "default_rate_limit" {
  type    = number
  default = 50
}

variable "default_burst_limit" {
  type    = number
  default = 100
}

variable "titan_model_id" {
  type    = string
  default = "amazon.titan-embed-text-v2:0"
}

# IAM roles that administer the module-created KMS key. Empty means the account
# root, so IAM policies decide.
variable "kms_admin_role_arns" {
  type    = list(string)
  default = []
}

# Browser origins allowed to PUT to presigned upload URLs. Empty means no CORS
# rule.
variable "cors_allowed_origins" {
  type    = list(string)
  default = []
  validation {
    condition     = !contains(var.cors_allowed_origins, "*")
    error_message = "cors_allowed_origins must list specific origins, not \"*\"."
  }
}

variable "sklearn_training_image" {
  type    = string
  default = null
}

variable "unknown_rate_threshold" {
  type    = number
  default = 0.5
}

variable "coverage_floor" {
  type    = number
  default = 0.2
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "configure_apigateway_account" {
  type    = bool
  default = true
}
