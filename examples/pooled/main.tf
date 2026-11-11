# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.70, < 7.0"
    }
  }
  backend "s3" {}
}

variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "release_version" {
  type    = string
  default = "0.1.0"
}

variable "log_retention_days" {
  type    = number
  default = 365
}

variable "cors_allowed_origins" {
  type    = list(string)
  default = []
}

variable "tier3_endpoints" {
  type = map(object({
    image_uri         = string
    model_data_url    = string
    memory_size_in_mb = number
    max_concurrency   = number
  }))
  default = {}
}

provider "aws" {
  region = var.region
}

module "docclass" {
  source               = "../../modules/doc-classification"
  name                 = "docclass"
  region               = var.region
  release_version      = var.release_version
  artifact_dir         = "${path.root}/../../dist"
  log_retention_days   = var.log_retention_days
  cors_allowed_origins = var.cors_allowed_origins
  enable_tier3         = length(var.tier3_endpoints) > 0
  tier3_endpoints      = var.tier3_endpoints
}

output "api_url" {
  value = module.docclass.api_url
}

output "admin_url" {
  value = module.docclass.admin_url
}

output "token_endpoint" {
  value = module.docclass.token_endpoint
}

output "event_bus_arn" {
  value = module.docclass.event_bus_arn
}

output "data_bucket" {
  value = module.docclass.data_bucket
}

output "artefact_bucket" {
  value = module.docclass.artefact_bucket
}

output "training_state_machine_arn" {
  value = module.docclass.training_state_machine_arn
}

output "tier3_endpoints" {
  value = module.docclass.tier3_endpoints
}
