# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

resource "aws_sns_topic" "textract" {
  name              = "${var.name}-textract"
  kms_master_key_id = local.kms_key_arn
  tags              = local.tags
}

resource "aws_sns_topic_subscription" "textract" {
  topic_arn = aws_sns_topic.textract.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.fn["textract_callback"].arn
}

resource "aws_lambda_permission" "sns" {
  statement_id  = "AllowTextractTopic"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["textract_callback"].function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.textract.arn
}
