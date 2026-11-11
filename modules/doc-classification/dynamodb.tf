# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

resource "aws_dynamodb_table" "jobs" {
  name         = "${var.name}-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "gsi_pk"
    type = "S"
  }
  attribute {
    name = "gsi_sk"
    type = "S"
  }
  global_secondary_index {
    name            = "by_hash"
    hash_key        = "gsi_pk"
    range_key       = "gsi_sk"
    projection_type = "ALL"
  }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = local.kms_key_arn
  }
  deletion_protection_enabled = true
  tags                        = local.tags
}

resource "aws_dynamodb_table" "decisions" {
  name         = "${var.name}-decisions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = local.kms_key_arn
  }
  deletion_protection_enabled = true
  tags                        = local.tags
}

resource "aws_dynamodb_table" "consumers" {
  name         = "${var.name}-consumers"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }
  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = local.kms_key_arn
  }
  deletion_protection_enabled = true
  tags                        = local.tags
}

resource "aws_dynamodb_table" "semaphore" {
  name         = "${var.name}-semaphore"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = local.kms_key_arn
  }
  tags = local.tags
}
