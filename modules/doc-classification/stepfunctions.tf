# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

data "aws_iam_policy_document" "states_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

resource "aws_cloudwatch_log_group" "states" {
  for_each          = toset(["document", "training"])
  name              = "/aws/vendedlogs/states/${var.name}-${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = local.kms_key_arn
  tags              = local.tags
}

resource "aws_iam_role" "document_sm" {
  name               = "${var.name}-document-sm"
  assume_role_policy = data.aws_iam_policy_document.states_trust.json
  tags               = local.tags
}

data "aws_iam_policy_document" "document_sm" {
  statement {
    sid       = "InvokeSteps"
    actions   = ["lambda:InvokeFunction"]
    resources = [for k in ["preflight", "dedup", "text_layer", "textract_start", "classify", "finalize"] : aws_lambda_function.fn[k].arn]
  }
  statement {
    sid       = "Leases"
    actions   = ["dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.semaphore.arn]
  }
  statement {
    sid       = "Notify"
    actions   = ["events:PutEvents"]
    resources = [aws_cloudwatch_event_bus.events.arn]
  }
  statement {
    sid       = "UseKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [local.kms_key_arn]
  }
  statement {
    sid       = "Delivery"
    actions   = ["logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups"]
    resources = ["*"]
  }
  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "document_sm" {
  name   = "document"
  role   = aws_iam_role.document_sm.id
  policy = data.aws_iam_policy_document.document_sm.json
}

resource "aws_sfn_state_machine" "document" {
  #checkov:skip=CKV_AWS_285:Execution logging is on at ERROR level; execution data is excluded so document text never reaches logs (PLAT-AC-26)
  name     = "${var.name}-document"
  role_arn = aws_iam_role.document_sm.arn
  type     = "STANDARD"
  definition = templatefile("${path.module}/asl/document.asl.json", {
    preflight_arn      = aws_lambda_function.fn["preflight"].arn
    dedup_arn          = aws_lambda_function.fn["dedup"].arn
    text_layer_arn     = aws_lambda_function.fn["text_layer"].arn
    textract_start_arn = aws_lambda_function.fn["textract_start"].arn
    classify_arn       = aws_lambda_function.fn["classify"].arn
    finalize_arn       = aws_lambda_function.fn["finalize"].arn
    semaphore_table    = aws_dynamodb_table.semaphore.name
    event_bus          = aws_cloudwatch_event_bus.events.name
  })
  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.states["document"].arn}:*"
    include_execution_data = false
    level                  = "ERROR"
  }
  tracing_configuration {
    enabled = true
  }
  tags       = local.tags
  depends_on = [aws_iam_role_policy.document_sm]
}

data "aws_iam_policy_document" "sagemaker_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "training" {
  name               = "${var.name}-training"
  assume_role_policy = data.aws_iam_policy_document.sagemaker_trust.json
  tags               = local.tags
}

data "aws_iam_policy_document" "training" {
  statement {
    sid       = "ReadLabels"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [local.data_arn, "${local.data_arn}/labelled/*"]
  }
  statement {
    sid       = "ReadSource"
    actions   = ["s3:GetObject"]
    resources = ["${local.artefacts_arn}/releases/*"]
  }
  statement {
    sid       = "WriteOutput"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${local.artefacts_arn}/training/*"]
  }
  statement {
    sid       = "ListArtefacts"
    actions   = ["s3:ListBucket"]
    resources = [local.artefacts_arn]
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
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
    resources = [local.sklearn_repo_arn]
  }
  statement {
    sid     = "Logs"
    actions = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = [
      "arn:${local.partition}:logs:${var.region}:${local.account}:log-group:/aws/sagemaker/TrainingJobs",
      "arn:${local.partition}:logs:${var.region}:${local.account}:log-group:/aws/sagemaker/TrainingJobs:*",
    ]
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

resource "aws_iam_role_policy" "training" {
  name   = "training"
  role   = aws_iam_role.training.id
  policy = data.aws_iam_policy_document.training.json
}

resource "aws_iam_role" "training_sm" {
  name               = "${var.name}-training-sm"
  assume_role_policy = data.aws_iam_policy_document.states_trust.json
  tags               = local.tags
}

data "aws_iam_policy_document" "training_sm" {
  statement {
    sid       = "TrainingJobs"
    actions   = ["sagemaker:CreateTrainingJob", "sagemaker:DescribeTrainingJob", "sagemaker:StopTrainingJob", "sagemaker:AddTags"]
    resources = ["arn:${local.partition}:sagemaker:${var.region}:${local.account}:training-job/*"]
  }
  statement {
    sid       = "SyncRule"
    actions   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
    resources = ["arn:${local.partition}:events:${var.region}:${local.account}:rule/StepFunctionsGetEventsForSageMakerTrainingJobsRule"]
  }
  statement {
    sid       = "PassTrainingRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.training.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["sagemaker.amazonaws.com"]
    }
  }
  statement {
    sid       = "InvokeSteps"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.fn["train_register"].arn, aws_lambda_function.fn["titan_index"].arn]
  }
  statement {
    sid       = "UseKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [local.kms_key_arn]
  }
  statement {
    sid       = "Delivery"
    actions   = ["logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups"]
    resources = ["*"]
  }
  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "training_sm" {
  name   = "training"
  role   = aws_iam_role.training_sm.id
  policy = data.aws_iam_policy_document.training_sm.json
}

resource "aws_sfn_state_machine" "training" {
  #checkov:skip=CKV_AWS_285:Execution logging is on at ERROR level; execution data is excluded so labelled text never reaches logs
  name     = "${var.name}-training"
  role_arn = aws_iam_role.training_sm.arn
  type     = "STANDARD"
  definition = templatefile("${path.module}/asl/training.asl.json", {
    training_role_arn = aws_iam_role.training.arn
    sklearn_image     = local.sklearn_image
    artefact_bucket   = aws_s3_bucket.this["artefacts"].id
    data_bucket       = aws_s3_bucket.this["data"].id
    release_version   = var.release_version
    kms_key_arn       = local.kms_key_arn
    register_arn      = aws_lambda_function.fn["train_register"].arn
    titan_index_arn   = aws_lambda_function.fn["titan_index"].arn
  })
  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.states["training"].arn}:*"
    include_execution_data = false
    level                  = "ERROR"
  }
  tracing_configuration {
    enabled = true
  }
  tags       = merge(local.tags, local.release_tags)
  depends_on = [aws_iam_role_policy.training_sm]
}
