# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
import re
from typing import Any

from service.common.logs import log
from service.common.settings import settings
from service.workflow.context import workflow_context

_KEY = re.compile(r"^inbox/(?P<ns>[a-z][a-z0-9-]{1,30})/(?P<job>[0-9a-f-]{36})$")


def parse_key(key: str) -> tuple[str, str] | None:
    m = _KEY.match(key)
    return (m.group("ns"), m.group("job")) if m else None


def handler(event: dict, context: Any = None, *, states: Any = None, jobs: Any = None, s3: Any = None,
            state_machine_arn: str | None = None) -> dict:
    s = settings()
    detail = event.get("detail") or {}
    bucket = (detail.get("bucket") or {}).get("name")
    key = (detail.get("object") or {}).get("key", "")
    parsed = parse_key(key)
    if not parsed:
        log("ignored_object", reason="unexpected key")
        return {"started": False, "reason": "unexpected key"}
    ns, job_id = parsed
    ctx = workflow_context(ns, s3=s3, jobs=jobs, bucket=bucket)
    job = ctx.jobs.get(ns, job_id)
    if not job or job["status"] != "Pending":
        log("ignored_object", namespace=ns, job_id=job_id, reason="no pending job")
        return {"started": False, "reason": "no pending job"}
    if states is None:
        import boto3
        from service.common.aws import config
        states = boto3.client("stepfunctions", region_name=s.region, config=config(5, 3))
    payload = json.dumps({"namespace": ns, "job_id": job_id, "bucket": ctx.bucket, "key": key}, sort_keys=True)
    try:
        resp = states.start_execution(stateMachineArn=state_machine_arn or s.state_machine_arn, name=job_id,
                                      input=payload)
        log("execution_started", namespace=ns, job_id=job_id, execution=resp["executionArn"])
        return {"started": True, "execution": resp["executionArn"]}
    except states.exceptions.ExecutionAlreadyExists:
        log("execution_exists", namespace=ns, job_id=job_id)
        return {"started": False, "reason": "already started"}
