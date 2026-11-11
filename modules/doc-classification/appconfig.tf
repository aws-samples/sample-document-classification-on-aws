# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

resource "aws_appconfig_application" "this" {
  name = var.name
  tags = local.tags
}

resource "aws_appconfig_deployment_strategy" "this" {
  name                           = "${var.name}-gradual"
  deployment_duration_in_minutes = 10
  final_bake_time_in_minutes     = 15
  growth_factor                  = 20
  growth_type                    = "LINEAR"
  replicate_to                   = "NONE"
  tags                           = local.tags
}
