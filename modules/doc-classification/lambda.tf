# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

locals {
  functions = {
    api_jobs          = { handler = "service.api.jobs.handler", memory = 512, timeout = 15, storage = 512 }
    api_labels        = { handler = "service.api.labels.handler", memory = 512, timeout = 15, storage = 512 }
    api_admin         = { handler = "service.api.admin.handler", memory = 512, timeout = 60, storage = 512 }
    classify          = { handler = "service.classify.handler.handler", memory = 2048, timeout = 60, storage = 512 }
    starter           = { handler = "service.workflow.starter.handler", memory = 256, timeout = 15, storage = 512 }
    preflight         = { handler = "service.text.preflight.handler", memory = 1536, timeout = 300, storage = 2048 }
    dedup             = { handler = "service.workflow.dedup.handler", memory = 256, timeout = 15, storage = 512 }
    text_layer        = { handler = "service.text.text_layer.handler", memory = 1536, timeout = 300, storage = 2048 }
    textract_start    = { handler = "service.text.textract_start.handler", memory = 256, timeout = 30, storage = 512 }
    textract_callback = { handler = "service.text.textract_callback.handler", memory = 1024, timeout = 300, storage = 512 }
    finalize          = { handler = "service.workflow.finalize.handler", memory = 256, timeout = 15, storage = 512 }
    reaper            = { handler = "service.workflow.reaper.handler", memory = 256, timeout = 120, storage = 512 }
    train_register    = { handler = "service.training.register.handler", memory = 1024, timeout = 300, storage = 1024 }
    titan_index       = { handler = "service.training.titan_index.handler", memory = 2048, timeout = 900, storage = 1024 }
    propose_config    = { handler = "service.training.propose_config.handler", memory = 256, timeout = 60, storage = 512 }
  }
  data_access_users = ["api_jobs", "api_labels", "classify", "starter", "preflight", "dedup", "text_layer", "textract_callback", "finalize", "titan_index"]
  appconfig_readers = ["api_labels", "classify", "dedup"]
  common_env = {
    DATA_BUCKET                   = aws_s3_bucket.this["data"].id
    ARTEFACT_BUCKET               = aws_s3_bucket.this["artefacts"].id
    JOBS_TABLE                    = aws_dynamodb_table.jobs.name
    DECISIONS_TABLE               = aws_dynamodb_table.decisions.name
    CONSUMERS_TABLE               = aws_dynamodb_table.consumers.name
    SEMAPHORE_TABLE               = aws_dynamodb_table.semaphore.name
    EVENT_BUS                     = aws_cloudwatch_event_bus.events.name
    DATA_ACCESS_ROLE_ARN          = aws_iam_role.data_access.arn
    TEXTRACT_TOPIC_ARN            = aws_sns_topic.textract.arn
    TEXTRACT_ROLE_ARN             = aws_iam_role.textract_publish.arn
    APPCONFIG_APPLICATION         = aws_appconfig_application.this.id
    APPCONFIG_DEPLOYMENT_STRATEGY = aws_appconfig_deployment_strategy.this.id
    APPCONFIG_MONITOR_ROLE_ARN    = aws_iam_role.appconfig_monitor.arn
    USER_POOL_ID                  = aws_cognito_user_pool.consumers.id
    RESOURCE_SERVER               = aws_cognito_resource_server.docclass.identifier
    API_ID                        = aws_api_gateway_rest_api.this.id
    API_STAGE                     = local.api_stage
    ALARM_TOPIC_ARN               = var.alarm_sns_topic_arn == null ? "" : var.alarm_sns_topic_arn
    METRIC_NAMESPACE              = local.metric_ns
  }
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.functions
  name              = "/aws/lambda/${var.name}-${replace(each.key, "_", "-")}"
  retention_in_days = var.log_retention_days
  kms_key_id        = local.kms_key_arn
  tags              = local.tags
}

resource "aws_lambda_function" "fn" {
  #checkov:skip=CKV_AWS_116:Functions are invoked synchronously by API Gateway and Step Functions, which handle retries and failures
  #checkov:skip=CKV_AWS_272:Code signing needs an AWS Signer profile, which is outside the scope of this sample
  #checkov:skip=CKV_AWS_115:Concurrency is bounded per consumer by API Gateway usage plans and the namespace semaphore
  for_each         = local.functions
  function_name    = "${var.name}-${replace(each.key, "_", "-")}"
  role             = aws_iam_role.fn[each.key].arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = each.value.handler
  memory_size      = each.value.memory
  timeout          = each.value.timeout
  s3_bucket        = aws_s3_object.service_zip.bucket
  s3_key           = aws_s3_object.service_zip.key
  source_code_hash = try(filebase64sha256(local.service_zip), null)
  kms_key_arn      = local.kms_key_arn
  ephemeral_storage {
    size = each.value.storage
  }
  environment {
    variables = merge(local.common_env, {
      STATE_MACHINE_ARN = each.key == "starter" ? "arn:${local.partition}:states:${var.region}:${local.account}:stateMachine:${var.name}-document" : ""
    })
  }
  tracing_config {
    mode = "Active"
  }
  dynamic "vpc_config" {
    for_each = local.in_vpc ? [1] : []
    content {
      subnet_ids         = var.subnet_ids
      security_group_ids = [aws_security_group.lambda[0].id]
    }
  }
  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.fn[each.key].name
  }
  tags       = merge(local.tags, local.release_tags)
  depends_on = [aws_cloudwatch_log_group.fn, aws_iam_role_policy.fn]
}

resource "aws_lambda_permission" "api" {
  for_each      = toset(["api_jobs", "api_labels", "api_admin", "classify"])
  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn[each.key].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.this.execution_arn}/*/*"
}
