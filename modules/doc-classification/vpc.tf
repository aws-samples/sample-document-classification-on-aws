# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

locals {
  interface_services = ["sts", "bedrock-runtime", "sagemaker.runtime", "sagemaker.api", "textract", "states", "events", "appconfig", "appconfigdata", "cognito-idp", "apigateway", "monitoring", "logs", "sns", "xray", "kms"]
  gateway_services   = ["s3", "dynamodb"]
}

data "aws_vpc" "byo" {
  count = local.in_vpc ? 1 : 0
  id    = var.vpc_id
}

resource "aws_security_group" "lambda" {
  count       = local.in_vpc ? 1 : 0
  name        = "${var.name}-lambda"
  description = "docclass functions"
  vpc_id      = var.vpc_id
  egress {
    description = "AWS endpoints inside the VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.byo[0].cidr_block]
  }
  egress {
    description     = "Gateway endpoints"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    prefix_list_ids = [for s in local.gateway_services : aws_vpc_endpoint.gateway[s].prefix_list_id]
  }
  tags = local.tags
}

resource "aws_security_group" "endpoints" {
  count       = local.in_vpc ? 1 : 0
  name        = "${var.name}-endpoints"
  description = "docclass interface endpoints"
  vpc_id      = var.vpc_id
  ingress {
    description     = "From docclass functions"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.lambda[0].id]
  }
  tags = local.tags
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = local.in_vpc ? toset(local.interface_services) : toset([])
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = [aws_security_group.endpoints[0].id]
  private_dns_enabled = true
  tags                = local.tags
}

resource "aws_vpc_endpoint" "gateway" {
  for_each          = local.in_vpc ? toset(local.gateway_services) : toset([])
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = var.route_table_ids
  tags              = local.tags
}
