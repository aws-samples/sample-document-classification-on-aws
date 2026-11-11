# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

resource "aws_cognito_user_pool" "consumers" {
  name                = "${var.name}-consumers"
  deletion_protection = "ACTIVE"
  mfa_configuration   = "OFF"
  admin_create_user_config {
    allow_admin_create_user_only = true
  }
  password_policy {
    minimum_length    = 16
    require_lowercase = true
    require_numbers   = true
    require_symbols   = true
    require_uppercase = true
  }
  tags = local.tags
}

resource "aws_cognito_user_pool_domain" "consumers" {
  domain       = "${var.name}-${local.account}"
  user_pool_id = aws_cognito_user_pool.consumers.id
}

resource "aws_cognito_resource_server" "docclass" {
  identifier   = "docclass"
  name         = "docclass"
  user_pool_id = aws_cognito_user_pool.consumers.id
  scope {
    scope_name        = "classify:write"
    scope_description = "submit documents for classification"
  }
  scope {
    scope_name        = "jobs:read"
    scope_description = "read job status and results"
  }
  scope {
    scope_name        = "labels:write"
    scope_description = "record human labels"
  }
}
