# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

output "api_url" {
  value = "${aws_api_gateway_stage.live.invoke_url}/v1"
}

output "admin_url" {
  value = "${aws_api_gateway_stage.live.invoke_url}/admin/consumers"
}

output "token_endpoint" {
  value = "https://${aws_cognito_user_pool_domain.consumers.domain}.auth.${var.region}.amazoncognito.com/oauth2/token"
}

output "cognito_issuer" {
  value = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.consumers.id}"
}

output "event_bus_arn" {
  value = aws_cloudwatch_event_bus.events.arn
}

output "data_bucket" {
  value = aws_s3_bucket.this["data"].id
}

output "artefact_bucket" {
  value = aws_s3_bucket.this["artefacts"].id
}

output "document_state_machine_arn" {
  value = aws_sfn_state_machine.document.arn
}

output "training_state_machine_arn" {
  value = aws_sfn_state_machine.training.arn
}

output "kms_key_arn" {
  value = local.kms_key_arn
}

output "tier3_endpoints" {
  value = { for k, v in aws_sagemaker_endpoint.tier3 : k => v.name }
}
