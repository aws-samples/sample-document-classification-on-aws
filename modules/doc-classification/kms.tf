# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

data "aws_iam_policy_document" "kms" {
  count = var.kms_key_arn == null ? 1 : 0
  # Administration and use are separate. Key administrators default to the
  # account root, which delegates to IAM, so whoever runs Terraform can manage
  # the key. Set kms_admin_role_arns to name the administrators instead, and
  # include the role that runs Terraform, or it can no longer update the key.
  statement {
    sid = "KeyAdministration"
    actions = [
      "kms:Create*", "kms:Describe*", "kms:Enable*", "kms:List*", "kms:Put*", "kms:Update*", "kms:Revoke*",
      "kms:Disable*", "kms:Get*", "kms:Delete*", "kms:TagResource", "kms:UntagResource",
      "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion", "kms:RotateKeyOnDemand",
    ]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = length(var.kms_admin_role_arns) > 0 ? var.kms_admin_role_arns : ["arn:${local.partition}:iam::${local.account}:root"]
    }
  }
  # Use is granted through IAM: only principals whose IAM policies allow these
  # actions on the key can use it.
  statement {
    sid       = "KeyUseThroughIam"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account}:root"]
    }
  }
  statement {
    sid       = "GrantsForAwsServices"
    actions   = ["kms:CreateGrant", "kms:ListGrants", "kms:RevokeGrant"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account}:root"]
    }
    condition {
      test     = "Bool"
      variable = "kms:GrantIsForAWSResource"
      values   = ["true"]
    }
  }
  statement {
    sid       = "CloudWatchLogs"
    actions   = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${var.region}.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:${local.partition}:logs:${var.region}:${local.account}:log-group:*"]
    }
  }
}

resource "aws_kms_key" "main" {
  count               = var.kms_key_arn == null ? 1 : 0
  description         = "docclass ${var.name}"
  enable_key_rotation = true
  policy              = data.aws_iam_policy_document.kms[0].json
  tags                = local.tags
}

resource "aws_kms_alias" "main" {
  count         = var.kms_key_arn == null ? 1 : 0
  name          = "alias/${var.name}"
  target_key_id = aws_kms_key.main[0].key_id
}
