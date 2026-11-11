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
  type = string
}

variable "name" {
  type = string
}

variable "release_version" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "route_table_ids" {
  type = list(string)
}

variable "kms_key_arn" {
  type = string
}

variable "alarm_sns_topic_arn" {
  type = string
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
  source              = "../../modules/doc-classification"
  name                = var.name
  region              = var.region
  release_version     = var.release_version
  artifact_dir        = "${path.root}/../../dist"
  vpc_mode            = "byo"
  vpc_id              = var.vpc_id
  subnet_ids          = var.subnet_ids
  route_table_ids     = var.route_table_ids
  kms_key_arn         = var.kms_key_arn
  alarm_sns_topic_arn = var.alarm_sns_topic_arn
  enable_tier3        = length(var.tier3_endpoints) > 0
  tier3_endpoints     = var.tier3_endpoints
}

output "api_url" {
  value = module.docclass.api_url
}

output "tier3_endpoints" {
  value = module.docclass.tier3_endpoints
}
