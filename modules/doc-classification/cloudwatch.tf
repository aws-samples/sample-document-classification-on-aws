# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

locals {
  tiers = ["char_ngram_lr", "titan_nn", "sagemaker"]
}

resource "aws_cloudwatch_metric_alarm" "unknown_rate" {
  alarm_name          = "${local.metric_ns}-${var.name}-unknown-rate"
  namespace           = local.metric_ns
  metric_name         = "UnknownRate"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  threshold           = var.unknown_rate_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  tags                = local.tags
}

resource "aws_cloudwatch_metric_alarm" "coverage" {
  for_each            = toset(local.tiers)
  alarm_name          = "${local.metric_ns}-${var.name}-coverage-${each.key}"
  evaluation_periods  = 6
  datapoints_to_alarm = 4
  threshold           = var.coverage_floor
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  metric_query {
    id          = "coverage"
    expression  = "IF(attempts > 0, accepted / attempts, 1)"
    label       = "coverage ${each.key}"
    return_data = true
  }
  metric_query {
    id = "accepted"
    metric {
      namespace   = local.metric_ns
      metric_name = "Accepted"
      period      = 900
      stat        = "Sum"
      dimensions  = { tier = each.key }
    }
  }
  metric_query {
    id = "attempts"
    metric {
      namespace   = local.metric_ns
      metric_name = "Attempts"
      period      = 900
      stat        = "Sum"
      dimensions  = { tier = each.key }
    }
  }
  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "throttled" {
  for_each            = toset(["titan_nn", "sagemaker"])
  alarm_name          = "${local.metric_ns}-${var.name}-throttled-${each.key}"
  namespace           = local.metric_ns
  metric_name         = "Throttled"
  dimensions          = { tier = each.key }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  tags                = local.tags
}

resource "aws_cloudwatch_metric_alarm" "state_machine_failures" {
  alarm_name          = "${local.metric_ns}-${var.name}-document-failures"
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  threshold           = 0.05
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  metric_query {
    id          = "rate"
    expression  = "IF(started > 0, failed / started, 0)"
    label       = "failure rate"
    return_data = true
  }
  metric_query {
    id = "failed"
    metric {
      namespace   = "AWS/States"
      metric_name = "ExecutionsFailed"
      period      = 300
      stat        = "Sum"
      dimensions  = { StateMachineArn = aws_sfn_state_machine.document.arn }
    }
  }
  metric_query {
    id = "started"
    metric {
      namespace   = "AWS/States"
      metric_name = "ExecutionsStarted"
      period      = 300
      stat        = "Sum"
      dimensions  = { StateMachineArn = aws_sfn_state_machine.document.arn }
    }
  }
  tags = local.tags
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.name}-docclass"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title   = "Unknown rate"
          region  = var.region
          stat    = "Average"
          period  = 300
          metrics = [[local.metric_ns, "UnknownRate"]]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title   = "Accepted by tier"
          region  = var.region
          stat    = "Sum"
          period  = 300
          metrics = [for t in local.tiers : [local.metric_ns, "Accepted", "tier", t]]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title   = "Estimated cost (USD)"
          region  = var.region
          stat    = "Sum"
          period  = 3600
          metrics = [[local.metric_ns, "EstimatedCostUSD"]]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title  = "Document executions"
          region = var.region
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/States", "ExecutionsStarted", "StateMachineArn", aws_sfn_state_machine.document.arn],
            ["AWS/States", "ExecutionsFailed", "StateMachineArn", aws_sfn_state_machine.document.arn],
          ]
        }
      },
    ]
  })
}
