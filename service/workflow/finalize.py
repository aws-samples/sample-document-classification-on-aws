# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
from typing import Any

from service.common.jobs import failure_detail
from service.common.logs import log
from service.workflow.context import workflow_context


def _reason(error: Any) -> str:
    if isinstance(error, dict):
        name = str(error.get("Error") or "Error")
        cause = error.get("Cause")
        if isinstance(cause, str):
            try:
                parsed = json.loads(cause)
                name = str(parsed.get("errorType") or name)
            except ValueError:
                pass
        return name[:100]
    return str(error or "Error")[:100]


def handler(event: dict, context: Any = None, *, jobs: Any = None, s3: Any = None, bucket: str | None = None) -> dict:
    ns, job_id = event["namespace"], event["job_id"]
    ctx = workflow_context(ns, s3=s3, jobs=jobs, bucket=bucket)
    if event.get("action") == "rejected":
        status, reason = "Rejected", str(event.get("reason") or "rejected")[:200]
    else:
        status, reason = "Failed", _reason(event.get("error"))
        ctx.jobs.set_status(ns, job_id, "Failed", reason=reason)
    log("job_ended", namespace=ns, job_id=job_id, status=status, reason=reason)
    return failure_detail(ns, job_id, status, reason)
