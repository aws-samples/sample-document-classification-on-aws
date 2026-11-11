# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

locals {
  data_arn      = aws_s3_bucket.this["data"].arn
  artefacts_arn = aws_s3_bucket.this["artefacts"].arn
  appconfig_arn = "arn:${local.partition}:appconfig:${var.region}:${local.account}:application/${aws_appconfig_application.this.id}"
  doc_sm_arn    = "arn:${local.partition}:states:${var.region}:${local.account}:stateMachine:${var.name}-document"
  doc_exec_arn  = "arn:${local.partition}:states:${var.region}:${local.account}:execution:${var.name}-document:*"
  endpoint_arn  = "arn:${local.partition}:sagemaker:${var.region}:${local.account}:endpoint/${var.name}-*"
  package_arn   = "arn:${local.partition}:sagemaker:${var.region}:${local.account}:model-package/*"
  group_arn     = "arn:${local.partition}:sagemaker:${var.region}:${local.account}:model-package-group/*"
  alarm_arn     = "arn:${local.partition}:cloudwatch:${var.region}:${local.account}:alarm:${local.metric_ns}-*"
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fn" {
  for_each             = local.functions
  name                 = "${var.name}-${replace(each.key, "_", "-")}"
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  max_session_duration = 3600
  tags                 = local.tags
}

resource "aws_iam_role_policy_attachment" "vpc" {
  for_each   = local.in_vpc ? local.functions : {}
  role       = aws_iam_role.fn[each.key].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "fn" {
  for_each = local.functions

  statement {
    sid       = "OwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.fn[each.key].arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }

  dynamic "statement" {
    for_each = contains(local.data_access_users, each.key) ? [1] : []
    content {
      sid       = "AssumeDataAccess"
      actions   = ["sts:AssumeRole", "sts:TagSession"]
      resources = [aws_iam_role.data_access.arn]
    }
  }

  dynamic "statement" {
    for_each = contains(local.appconfig_readers, each.key) ? [1] : []
    content {
      sid       = "ReadAppConfig"
      actions   = ["appconfig:StartConfigurationSession", "appconfig:GetLatestConfiguration"]
      resources = ["${local.appconfig_arn}/*"]
    }
  }

  dynamic "statement" {
    for_each = contains(["api_jobs", "api_labels", "classify", "preflight"], each.key) ? [1] : []
    content {
      sid       = "ReadConsumers"
      actions   = ["dynamodb:GetItem"]
      resources = [aws_dynamodb_table.consumers.arn]
    }
  }

  dynamic "statement" {
    for_each = contains(["classify", "titan_index"], each.key) ? [1] : []
    content {
      sid       = "InvokeTitan"
      actions   = ["bedrock:InvokeModel"]
      resources = [local.titan_model_arn]
    }
  }

  dynamic "statement" {
    for_each = each.key == "classify" && var.enable_tier3 ? [1] : []
    content {
      sid       = "InvokeTier3"
      actions   = ["sagemaker:InvokeEndpoint"]
      resources = [local.endpoint_arn]
    }
  }

  dynamic "statement" {
    for_each = each.key == "starter" ? [1] : []
    content {
      sid       = "StartDocumentExecution"
      actions   = ["states:StartExecution"]
      resources = [local.doc_sm_arn]
    }
  }

  dynamic "statement" {
    for_each = each.key == "textract_start" ? [1] : []
    content {
      sid       = "StartTextract"
      actions   = ["textract:StartDocumentTextDetection"]
      resources = ["*"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "textract_start" ? [1] : []
    content {
      sid       = "ReadOcrInput"
      actions   = ["s3:GetObject"]
      resources = ["${local.data_arn}/text/*.ocr.pdf"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "textract_start" ? [1] : []
    content {
      sid       = "PassTextractRole"
      actions   = ["iam:PassRole"]
      resources = [aws_iam_role.textract_publish.arn]
      condition {
        test     = "StringEquals"
        variable = "iam:PassedToService"
        values   = ["textract.amazonaws.com"]
      }
    }
  }

  dynamic "statement" {
    for_each = contains(["textract_start", "textract_callback"], each.key) ? [1] : []
    content {
      sid       = "TextractMapping"
      actions   = each.key == "textract_start" ? ["dynamodb:PutItem"] : ["dynamodb:GetItem", "dynamodb:DeleteItem"]
      resources = [aws_dynamodb_table.jobs.arn]
      condition {
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["textract#*"]
      }
    }
  }

  dynamic "statement" {
    for_each = each.key == "textract_callback" ? [1] : []
    content {
      sid       = "ReadTextract"
      actions   = ["textract:GetDocumentTextDetection"]
      resources = ["*"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "textract_callback" ? [1] : []
    content {
      sid       = "ResumeExecution"
      actions   = ["states:SendTaskSuccess", "states:SendTaskFailure"]
      resources = [local.doc_sm_arn]
    }
  }

  dynamic "statement" {
    for_each = each.key == "reaper" ? [1] : []
    content {
      sid       = "Leases"
      actions   = ["dynamodb:Scan", "dynamodb:UpdateItem"]
      resources = [aws_dynamodb_table.semaphore.arn]
    }
  }

  dynamic "statement" {
    for_each = each.key == "reaper" ? [1] : []
    content {
      sid       = "DescribeExecutions"
      actions   = ["states:DescribeExecution"]
      resources = [local.doc_exec_arn]
    }
  }

  dynamic "statement" {
    for_each = each.key == "api_admin" ? [1] : []
    content {
      sid       = "CreateAppClients"
      actions   = ["cognito-idp:CreateUserPoolClient"]
      resources = [aws_cognito_user_pool.consumers.arn]
    }
  }

  dynamic "statement" {
    for_each = each.key == "api_admin" ? [1] : []
    content {
      sid     = "ApiKeysAndPlans"
      actions = ["apigateway:POST", "apigateway:GET"]
      resources = [
        "arn:${local.partition}:apigateway:${var.region}::/apikeys",
        "arn:${local.partition}:apigateway:${var.region}::/apikeys/*",
        "arn:${local.partition}:apigateway:${var.region}::/usageplans",
        "arn:${local.partition}:apigateway:${var.region}::/usageplans/*",
      ]
    }
  }

  dynamic "statement" {
    for_each = each.key == "api_admin" ? [1] : []
    content {
      sid       = "NamespaceAlarms"
      actions   = ["cloudwatch:PutMetricAlarm", "cloudwatch:DescribeAlarms", "cloudwatch:SetAlarmState"]
      resources = [local.alarm_arn]
    }
  }

  dynamic "statement" {
    for_each = each.key == "api_admin" ? [1] : []
    content {
      sid       = "NamespaceConfig"
      actions   = ["appconfig:CreateConfigurationProfile", "appconfig:CreateEnvironment", "appconfig:CreateHostedConfigurationVersion", "appconfig:StartDeployment"]
      resources = [local.appconfig_arn, "${local.appconfig_arn}/*", "arn:${local.partition}:appconfig:${var.region}:${local.account}:deploymentstrategy/${aws_appconfig_deployment_strategy.this.id}"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "api_admin" ? [1] : []
    content {
      sid       = "PassMonitorRole"
      actions   = ["iam:PassRole"]
      resources = [aws_iam_role.appconfig_monitor.arn]
    }
  }

  dynamic "statement" {
    for_each = each.key == "api_admin" ? [1] : []
    content {
      sid       = "WriteConsumers"
      actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
      resources = [aws_dynamodb_table.consumers.arn]
    }
  }

  dynamic "statement" {
    for_each = each.key == "train_register" ? [1] : []
    content {
      sid       = "TrainingOutput"
      actions   = ["s3:GetObject"]
      resources = ["${local.artefacts_arn}/training/*", "${local.artefacts_arn}/artefacts/*"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "train_register" ? [1] : []
    content {
      sid       = "PublishArtefacts"
      actions   = ["s3:PutObject"]
      resources = ["${local.artefacts_arn}/artefacts/*"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "train_register" ? [1] : []
    content {
      sid       = "RegisterModels"
      actions   = ["sagemaker:CreateModelPackage", "sagemaker:CreateModelPackageGroup", "sagemaker:DescribeModelPackageGroup"]
      resources = [local.package_arn, local.group_arn]
    }
  }

  dynamic "statement" {
    for_each = each.key == "propose_config" ? [1] : []
    content {
      sid       = "ReadPackages"
      actions   = ["sagemaker:DescribeModelPackage"]
      resources = [local.package_arn]
    }
  }

  dynamic "statement" {
    for_each = each.key == "propose_config" ? [1] : []
    content {
      sid       = "ProposeConfig"
      actions   = ["appconfig:ListHostedConfigurationVersions", "appconfig:GetHostedConfigurationVersion", "appconfig:CreateHostedConfigurationVersion"]
      resources = ["${local.appconfig_arn}/*"]
    }
  }

  dynamic "statement" {
    for_each = each.key == "propose_config" ? [1] : []
    content {
      sid       = "ReadConsumerProfile"
      actions   = ["dynamodb:GetItem"]
      resources = [aws_dynamodb_table.consumers.arn]
    }
  }

  dynamic "statement" {
    for_each = contains(["textract_start", "train_register"], each.key) ? [1] : []
    content {
      sid       = "UseKey"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
      resources = [local.kms_key_arn]
    }
  }

  dynamic "statement" {
    for_each = contains(["api_jobs", "api_labels", "api_admin", "classify", "preflight", "textract_start", "textract_callback", "reaper", "propose_config"], each.key) ? [1] : []
    content {
      sid       = "TableKey"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
      resources = [local.kms_key_arn]
      condition {
        test     = "StringEquals"
        variable = "kms:ViaService"
        values   = ["dynamodb.${var.region}.amazonaws.com"]
      }
    }
  }
}

resource "aws_iam_role_policy" "fn" {
  for_each = local.functions
  name     = "least-privilege"
  role     = aws_iam_role.fn[each.key].id
  policy   = data.aws_iam_policy_document.fn[each.key].json
}

data "aws_iam_policy_document" "data_access_trust" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "AWS"
      identifiers = [for k in local.data_access_users : aws_iam_role.fn[k].arn]
    }
    condition {
      test     = "Null"
      variable = "aws:RequestTag/namespace"
      values   = ["false"]
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["namespace"]
    }
  }
}

resource "aws_iam_role" "data_access" {
  name                 = "${var.name}-data-access"
  assume_role_policy   = data.aws_iam_policy_document.data_access_trust.json
  max_session_duration = 3600
  tags                 = local.tags
}

data "aws_iam_policy_document" "data_access" {
  statement {
    sid     = "NamespaceObjects"
    actions = ["s3:GetObject", "s3:PutObject"]
    resources = [
      "${local.data_arn}/inbox/$${aws:PrincipalTag/namespace}/*",
      "${local.data_arn}/text/$${aws:PrincipalTag/namespace}/*",
      "${local.data_arn}/labelled/$${aws:PrincipalTag/namespace}/*",
      "${local.data_arn}/exemplars/$${aws:PrincipalTag/namespace}/*",
      "${local.artefacts_arn}/artefacts/$${aws:PrincipalTag/namespace}/*",
    ]
  }
  statement {
    sid       = "NamespaceListing"
    actions   = ["s3:ListBucket"]
    resources = [local.data_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["exemplars/$${aws:PrincipalTag/namespace}/*", "text/$${aws:PrincipalTag/namespace}/*"]
    }
  }
  statement {
    sid       = "NamespaceItems"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.jobs.arn, "${aws_dynamodb_table.jobs.arn}/index/by_hash", aws_dynamodb_table.decisions.arn]
    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values = [
        "ns#$${aws:PrincipalTag/namespace}#*",
        "$${aws:PrincipalTag/namespace}#*",
        "emb#$${aws:PrincipalTag/namespace}#*",
      ]
    }
  }
  statement {
    sid       = "EncryptedStorage"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [local.kms_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com", "dynamodb.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "data_access" {
  name   = "namespace-scoped"
  role   = aws_iam_role.data_access.id
  policy = data.aws_iam_policy_document.data_access.json
}

data "aws_iam_policy_document" "textract_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["textract.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

resource "aws_iam_role" "textract_publish" {
  name               = "${var.name}-textract-publish"
  assume_role_policy = data.aws_iam_policy_document.textract_trust.json
  tags               = local.tags
}

data "aws_iam_policy_document" "textract_publish" {
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.textract.arn]
  }
  statement {
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [local.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "textract_publish" {
  name   = "publish"
  role   = aws_iam_role.textract_publish.id
  policy = data.aws_iam_policy_document.textract_publish.json
}

data "aws_iam_policy_document" "appconfig_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["appconfig.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "appconfig_monitor" {
  name               = "${var.name}-appconfig-monitor"
  assume_role_policy = data.aws_iam_policy_document.appconfig_trust.json
  tags               = local.tags
}

data "aws_iam_policy_document" "appconfig_monitor" {
  statement {
    actions   = ["cloudwatch:DescribeAlarms"]
    resources = [local.alarm_arn]
  }
}

resource "aws_iam_role_policy" "appconfig_monitor" {
  name   = "describe-alarms"
  role   = aws_iam_role.appconfig_monitor.id
  policy = data.aws_iam_policy_document.appconfig_monitor.json
}
