# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

resource "aws_cloudwatch_event_bus" "events" {
  name = "${var.name}-events"
  tags = local.tags
}

resource "aws_cloudwatch_event_rule" "upload" {
  name = "${var.name}-upload"
  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.this["data"].id] }
      object = { key = [{ prefix = "inbox/" }] }
    }
  })
  tags = local.tags
}

resource "aws_cloudwatch_event_target" "upload" {
  rule = aws_cloudwatch_event_rule.upload.name
  arn  = aws_lambda_function.fn["starter"].arn
  retry_policy {
    maximum_retry_attempts       = 10
    maximum_event_age_in_seconds = 3600
  }
}

resource "aws_cloudwatch_event_rule" "model_approved" {
  name = "${var.name}-model-approved"
  event_pattern = jsonencode({
    source      = ["aws.sagemaker"]
    detail-type = ["SageMaker Model Package State Change"]
    detail = {
      ModelApprovalStatus = ["Approved"]
    }
  })
  tags = local.tags
}

resource "aws_cloudwatch_event_target" "model_approved" {
  rule = aws_cloudwatch_event_rule.model_approved.name
  arn  = aws_lambda_function.fn["propose_config"].arn
}

resource "aws_cloudwatch_event_rule" "reaper" {
  name                = "${var.name}-reaper"
  schedule_expression = "rate(15 minutes)"
  tags                = local.tags
}

resource "aws_cloudwatch_event_target" "reaper" {
  rule = aws_cloudwatch_event_rule.reaper.name
  arn  = aws_lambda_function.fn["reaper"].arn
}

resource "aws_lambda_permission" "events" {
  for_each = {
    starter        = aws_cloudwatch_event_rule.upload.arn
    propose_config = aws_cloudwatch_event_rule.model_approved.arn
    reaper         = aws_cloudwatch_event_rule.reaper.arn
  }
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn[each.key].function_name
  principal     = "events.amazonaws.com"
  source_arn    = each.value
}
