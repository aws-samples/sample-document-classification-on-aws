# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

locals {
  buckets = {
    data      = "${var.name}-data-${local.account}"
    artefacts = "${var.name}-artefacts-${local.account}"
  }
}

resource "aws_s3_bucket" "this" {
  #checkov:skip=CKV_AWS_145:SSE-KMS is set by aws_s3_bucket_server_side_encryption_configuration.this
  #checkov:skip=CKV2_AWS_6:Public access is blocked by aws_s3_bucket_public_access_block.this
  #checkov:skip=CKV_AWS_21:Versioning is enabled by aws_s3_bucket_versioning.this
  #checkov:skip=CKV2_AWS_61:Lifecycle rules are set by aws_s3_bucket_lifecycle_configuration.data and .artefacts
  #checkov:skip=CKV2_AWS_62:The data bucket sends events to EventBridge; the artefacts bucket has no consumers of object events
  #checkov:skip=CKV_AWS_18:Server access logging needs a separate log bucket, which is outside the scope of this sample
  #checkov:skip=CKV_AWS_144:Cross-Region replication is a deployment choice outside the scope of this sample
  for_each      = local.buckets
  bucket        = each.value
  force_destroy = false
  tags          = local.tags
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = local.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

data "aws_iam_policy_document" "tls_only" {
  for_each = aws_s3_bucket.this
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [each.value.arn, "${each.value.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  for_each   = aws_s3_bucket.this
  bucket     = each.value.id
  policy     = data.aws_iam_policy_document.tls_only[each.key].json
  depends_on = [aws_s3_bucket_public_access_block.this]
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.this["data"].id
  rule {
    id     = "expire-inbox"
    status = "Enabled"
    filter {
      prefix = "inbox/"
    }
    expiration {
      days = 30
    }
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }
  rule {
    id     = "expire-text"
    status = "Enabled"
    filter {
      prefix = "text/"
    }
    expiration {
      days = 30
    }
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }
  rule {
    id     = "abort-multipart"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artefacts" {
  bucket = aws_s3_bucket.this["artefacts"].id
  rule {
    id     = "noncurrent"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# Only browser uploads need CORS. API and CLI clients PUT to the presigned URL
# without it, so there is no rule unless origins are listed.
resource "aws_s3_bucket_cors_configuration" "data" {
  count  = length(var.cors_allowed_origins) > 0 ? 1 : 0
  bucket = aws_s3_bucket.this["data"].id
  cors_rule {
    allowed_methods = ["PUT"]
    allowed_origins = var.cors_allowed_origins
    allowed_headers = ["content-type"]
    max_age_seconds = 900
  }
}

resource "aws_s3_bucket_notification" "data" {
  bucket      = aws_s3_bucket.this["data"].id
  eventbridge = true
}

resource "aws_s3_object" "service_zip" {
  bucket      = aws_s3_bucket.this["artefacts"].id
  key         = "releases/${var.release_version}/service.zip"
  source      = local.service_zip
  source_hash = try(filemd5(local.service_zip), null)
  tags        = local.release_tags
}

resource "aws_s3_object" "training_source" {
  bucket      = aws_s3_bucket.this["artefacts"].id
  key         = "releases/${var.release_version}/training-source.tar.gz"
  source      = local.training_tgz
  source_hash = try(filemd5(local.training_tgz), null)
  tags        = local.release_tags
}
