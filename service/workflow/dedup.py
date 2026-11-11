# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from typing import Any

from service.common.jobs import event_detail
from service.common.logs import log
from service.workflow.context import workflow_context

_provider = None


def _config_provider() -> Any:
    global _provider
    if _provider is None:
        import boto3
        from service.common.appconfig import ConfigProvider
        from service.common.aws import config
        from service.common.settings import settings
        s = settings()
        _provider = ConfigProvider(boto3.client("appconfigdata", region_name=s.region, config=config(2, 3)),
                                   s.appconfig_application)
    return _provider


def handler(event: dict, context: Any = None, *, jobs: Any = None, s3: Any = None, bucket: str | None = None,
            config_provider: Any = None) -> dict:
    ns, job_id, doc_hash = event["namespace"], event["job_id"], event["doc_hash"]
    ctx = workflow_context(ns, s3=s3, jobs=jobs, bucket=bucket)
    config_version = (config_provider or _config_provider()).get(ns)["config_version"]
    hit = ctx.jobs.find_completed(ns, doc_hash, config_version)
    if not hit or hit["job_id"] == job_id:
        return {"hit": False, "config_version": config_version}
    result = hit["result"]
    status = ctx.jobs.complete(ns, job_id, result, doc_hash=doc_hash, config_version=config_version)
    log("dedup_hit", namespace=ns, job_id=job_id, doc_hash=doc_hash, status=status)
    return {"hit": True, "config_version": config_version, "status": status, **event_detail(ns, job_id, result)}
