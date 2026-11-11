# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

locals {
  routes = {
    jobs_post     = { path = "jobs", method = "POST", fn = "api_jobs", scope = "classify:write", auth = "COGNITO_USER_POOLS" }
    jobs_get      = { path = "job_id", method = "GET", fn = "api_jobs", scope = "jobs:read", auth = "COGNITO_USER_POOLS" }
    classify_post = { path = "classify", method = "POST", fn = "classify", scope = "classify:write", auth = "COGNITO_USER_POOLS" }
    labels_post   = { path = "labels", method = "POST", fn = "api_labels", scope = "labels:write", auth = "COGNITO_USER_POOLS" }
    admin_post    = { path = "consumers", method = "POST", fn = "api_admin", scope = "", auth = "AWS_IAM" }
  }
  resource_ids = {
    jobs      = aws_api_gateway_resource.jobs.id
    job_id    = aws_api_gateway_resource.job_id.id
    classify  = aws_api_gateway_resource.classify.id
    labels    = aws_api_gateway_resource.labels.id
    consumers = aws_api_gateway_resource.consumers.id
  }
}

resource "aws_api_gateway_rest_api" "this" {
  name           = "${var.name}-api"
  api_key_source = "HEADER"
  endpoint_configuration {
    types = ["REGIONAL"]
  }
  lifecycle {
    create_before_destroy = true
  }
  tags = local.tags
}

resource "aws_api_gateway_authorizer" "cognito" {
  name          = "consumers"
  rest_api_id   = aws_api_gateway_rest_api.this.id
  type          = "COGNITO_USER_POOLS"
  provider_arns = [aws_cognito_user_pool.consumers.arn]
}

resource "aws_api_gateway_resource" "v1" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_rest_api.this.root_resource_id
  path_part   = "v1"
}

resource "aws_api_gateway_resource" "jobs" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.v1.id
  path_part   = "jobs"
}

resource "aws_api_gateway_resource" "job_id" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.jobs.id
  path_part   = "{id}"
}

resource "aws_api_gateway_resource" "classify" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.v1.id
  path_part   = "classify"
}

resource "aws_api_gateway_resource" "labels" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.v1.id
  path_part   = "labels"
}

resource "aws_api_gateway_resource" "admin" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_rest_api.this.root_resource_id
  path_part   = "admin"
}

resource "aws_api_gateway_resource" "consumers" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "consumers"
}

resource "aws_api_gateway_method" "route" {
  #checkov:skip=CKV2_AWS_53:Request bodies are validated in the Lambda handlers against the documented schemas
  for_each             = local.routes
  rest_api_id          = aws_api_gateway_rest_api.this.id
  resource_id          = local.resource_ids[each.value.path]
  http_method          = each.value.method
  authorization        = each.value.auth
  authorizer_id        = each.value.auth == "COGNITO_USER_POOLS" ? aws_api_gateway_authorizer.cognito.id : null
  authorization_scopes = each.value.auth == "COGNITO_USER_POOLS" ? ["${aws_cognito_resource_server.docclass.identifier}/${each.value.scope}"] : null
  api_key_required     = each.value.auth == "COGNITO_USER_POOLS"
  request_parameters   = each.value.path == "job_id" ? { "method.request.path.id" = true } : null
}

resource "aws_api_gateway_integration" "route" {
  for_each                = local.routes
  rest_api_id             = aws_api_gateway_rest_api.this.id
  resource_id             = local.resource_ids[each.value.path]
  http_method             = aws_api_gateway_method.route[each.key].http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.fn[each.value.fn].invoke_arn
  timeout_milliseconds    = 29000
}

resource "aws_api_gateway_deployment" "this" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  triggers = {
    redeploy = sha1(jsonencode([local.routes, aws_api_gateway_integration.route, aws_api_gateway_method.route]))
  }
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/${var.name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = local.kms_key_arn
  tags              = local.tags
}

resource "aws_api_gateway_stage" "live" {
  #checkov:skip=CKV_AWS_120:Responses are per consumer and per job, so caching would serve stale or other consumers' results
  #checkov:skip=CKV2_AWS_51:Clients authenticate with OAuth2 client credentials and an API key; mutual TLS is out of scope
  #checkov:skip=CKV2_AWS_77:The associated web ACL includes AWSManagedRulesKnownBadInputsRuleSet, which covers Log4j
  rest_api_id          = aws_api_gateway_rest_api.this.id
  deployment_id        = aws_api_gateway_deployment.this.id
  stage_name           = local.api_stage
  xray_tracing_enabled = true
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({
      requestId = "$context.requestId"
      ip        = "$context.identity.sourceIp"
      method    = "$context.httpMethod"
      path      = "$context.resourcePath"
      status    = "$context.status"
      latency   = "$context.responseLatency"
      apiKeyId  = "$context.identity.apiKeyId"
      clientId  = "$context.authorizer.claims.client_id"
      wafStatus = "$context.waf.status"
    })
  }
  tags = local.tags
}

resource "aws_api_gateway_method_settings" "all" {
  #checkov:skip=CKV_AWS_225:Responses are per consumer and per job, so caching would serve stale or other consumers' results
  rest_api_id = aws_api_gateway_rest_api.this.id
  stage_name  = aws_api_gateway_stage.live.stage_name
  method_path = "*/*"
  settings {
    metrics_enabled        = true
    logging_level          = "ERROR"
    data_trace_enabled     = false
    throttling_rate_limit  = var.default_rate_limit
    throttling_burst_limit = var.default_burst_limit
  }
}

resource "aws_wafv2_web_acl" "api" {
  name  = "${var.name}-api"
  scope = "REGIONAL"
  default_action {
    allow {}
  }
  rule {
    name     = "common"
    priority = 1
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
        rule_action_override {
          name = "SizeRestrictions_BODY"
          action_to_use {
            count {}
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-common"
      sampled_requests_enabled   = true
    }
  }
  rule {
    name     = "bad-inputs"
    priority = 2
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-bad-inputs"
      sampled_requests_enabled   = true
    }
  }
  rule {
    name     = "rate"
    priority = 3
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-rate"
      sampled_requests_enabled   = true
    }
  }
  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name}-api"
    sampled_requests_enabled   = true
  }
  tags = local.tags
}

resource "aws_cloudwatch_log_group" "waf" {
  name              = "aws-waf-logs-${var.name}-api"
  retention_in_days = var.log_retention_days
  kms_key_id        = local.kms_key_arn
  tags              = local.tags
}

resource "aws_wafv2_web_acl_logging_configuration" "api" {
  resource_arn            = aws_wafv2_web_acl.api.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]
  redacted_fields {
    single_header {
      name = "authorization"
    }
  }
  redacted_fields {
    single_header {
      name = "x-api-key"
    }
  }
}

resource "aws_wafv2_web_acl_association" "api" {
  resource_arn = aws_api_gateway_stage.live.arn
  web_acl_arn  = aws_wafv2_web_acl.api.arn
}

data "aws_iam_policy_document" "apigw_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["apigateway.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apigw_logs" {
  count              = var.configure_apigateway_account ? 1 : 0
  name               = "${var.name}-apigw-logs"
  assume_role_policy = data.aws_iam_policy_document.apigw_trust.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "apigw_logs" {
  count      = var.configure_apigateway_account ? 1 : 0
  role       = aws_iam_role.apigw_logs[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
}

resource "aws_api_gateway_account" "this" {
  count               = var.configure_apigateway_account ? 1 : 0
  cloudwatch_role_arn = aws_iam_role.apigw_logs[0].arn
  depends_on          = [aws_iam_role_policy_attachment.apigw_logs]
}
