# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

resource "aws_iam_role" "tier3" {
  count              = var.enable_tier3 ? 1 : 0
  name               = "${var.name}-tier3"
  assume_role_policy = data.aws_iam_policy_document.sagemaker_trust.json
  tags               = local.tags
}

data "aws_iam_policy_document" "tier3" {
  count = var.enable_tier3 ? 1 : 0
  statement {
    sid       = "ModelData"
    actions   = ["s3:GetObject"]
    resources = ["${local.artefacts_arn}/training/*", "${local.artefacts_arn}/artefacts/*"]
  }
  statement {
    sid       = "UseKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:CreateGrant"]
    resources = [local.kms_key_arn]
  }
  statement {
    sid       = "EcrToken"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid       = "PullImage"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = local.tier3_repo_arns
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:${local.partition}:logs:${var.region}:${local.account}:log-group:/aws/sagemaker/Endpoints/${var.name}-*"]
  }
  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringLike"
      variable = "cloudwatch:namespace"
      values   = ["/aws/sagemaker/*"]
    }
  }
}

resource "aws_iam_role_policy" "tier3" {
  count  = var.enable_tier3 ? 1 : 0
  name   = "tier3"
  role   = aws_iam_role.tier3[0].id
  policy = data.aws_iam_policy_document.tier3[0].json
}

resource "aws_sagemaker_model" "tier3" {
  #checkov:skip=CKV_AWS_370:Serverless Inference does not support network isolation
  for_each = local.tier3
  # The endpoint configuration is create-before-destroy, which carries over to
  # this model, so a replacement needs a new name: suffix it with a hash of
  # everything that forces one.
  name               = "${var.name}-${each.key}-${substr(sha1(jsonencode([each.value.image_uri, each.value.model_data_url, aws_iam_role.tier3[0].arn])), 0, 8)}"
  execution_role_arn = aws_iam_role.tier3[0].arn
  primary_container {
    image          = each.value.image_uri
    model_data_url = each.value.model_data_url
  }
  tags = local.tags
}

resource "aws_sagemaker_endpoint_configuration" "tier3" {
  for_each    = local.tier3
  name_prefix = "${var.name}-${each.key}-"
  kms_key_arn = local.kms_key_arn
  production_variants {
    variant_name = "primary"
    model_name   = aws_sagemaker_model.tier3[each.key].name
    serverless_config {
      memory_size_in_mb = each.value.memory_size_in_mb
      max_concurrency   = each.value.max_concurrency
    }
  }
  lifecycle {
    create_before_destroy = true
  }
  tags = local.tags
}

resource "aws_sagemaker_endpoint" "tier3" {
  for_each             = local.tier3
  name                 = "${var.name}-${each.key}"
  endpoint_config_name = aws_sagemaker_endpoint_configuration.tier3[each.key].name
  tags                 = local.tags
}

moved {
  from = aws_iam_role.tier4
  to   = aws_iam_role.tier3
}

moved {
  from = aws_iam_role_policy.tier4
  to   = aws_iam_role_policy.tier3
}

moved {
  from = aws_sagemaker_model.tier4
  to   = aws_sagemaker_model.tier3
}

moved {
  from = aws_sagemaker_endpoint_configuration.tier4
  to   = aws_sagemaker_endpoint_configuration.tier3
}

moved {
  from = aws_sagemaker_endpoint.tier4
  to   = aws_sagemaker_endpoint.tier3
}
