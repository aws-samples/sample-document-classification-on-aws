# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account       = data.aws_caller_identity.current.account_id
  partition     = data.aws_partition.current.partition
  artifact_dir  = coalesce(var.artifact_dir, "${path.root}/dist")
  release_dir   = "${local.artifact_dir}/${var.release_version}"
  service_zip   = "${local.release_dir}/service.zip"
  training_tgz  = "${local.release_dir}/training-source.tar.gz"
  kms_key_arn   = var.kms_key_arn != null ? var.kms_key_arn : aws_kms_key.main[0].arn
  in_vpc        = var.vpc_mode == "byo"
  tier3         = var.enable_tier3 ? var.tier3_endpoints : {}
  api_stage     = "live"
  metric_ns     = "DocClass"
  alarm_actions = var.alarm_sns_topic_arn == null ? [] : [var.alarm_sns_topic_arn]
  tags = merge(var.tags, {
    "docclass:deployment" = var.name
  })
  release_tags = {
    "docclass:release" = var.release_version
  }
  sklearn_accounts = {
    "us-east-1"      = "683313688378"
    "us-east-2"      = "257758044811"
    "us-west-2"      = "246618743249"
    "eu-west-1"      = "141502667606"
    "eu-west-2"      = "764974769150"
    "eu-central-1"   = "492215442770"
    "ap-southeast-2" = "783357654285"
  }
  sklearn_image    = coalesce(var.sklearn_training_image, "${lookup(local.sklearn_accounts, var.region, "683313688378")}.dkr.ecr.${var.region}.amazonaws.com/sagemaker-scikit-learn:1.4-2-cpu-py3")
  titan_model_arn  = "arn:${local.partition}:bedrock:${var.region}::foundation-model/${var.titan_model_id}"
  ecr_image        = "^(?P<account>[0-9]{12})\\.dkr\\.ecr\\.(?P<region>[a-z0-9-]+)\\.amazonaws\\.com(?:\\.cn)?/(?P<repo>[^:@]+)"
  sklearn_repo     = regex(local.ecr_image, local.sklearn_image)
  sklearn_repo_arn = "arn:${local.partition}:ecr:${local.sklearn_repo.region}:${local.sklearn_repo.account}:repository/${local.sklearn_repo.repo}"
  tier3_repo_arns = distinct([for m in [for e in values(local.tier3) : regex(local.ecr_image, e.image_uri)] :
  "arn:${local.partition}:ecr:${m.region}:${m.account}:repository/${m.repo}"])
}
