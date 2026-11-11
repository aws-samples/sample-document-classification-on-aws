# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import uuid
from typing import Any

from service.common.http import HttpError, error_response, path_param, response
from service.common.identity import resolve
from service.common.jobs import public_view
from service.common.logs import log
from service.common.storage import inbox_key

URL_TTL_S = 900


def _deps(deps: Any) -> Any:
    if deps is None:
        from service.api.deps import api_deps
        return api_deps()
    return deps


def create_job(event: dict, deps: Any = None) -> dict:
    d = _deps(deps)
    ident = resolve(event, d.consumers, "classify:write")
    job_id = str(uuid.uuid4())
    d.jobs(ident.namespace).create(ident.namespace, job_id)
    key = inbox_key(ident.namespace, job_id)
    url = d.s3(ident.namespace).generate_presigned_url(
        "put_object",
        Params={"Bucket": d.settings.data_bucket, "Key": key, "ContentType": "application/pdf"},
        ExpiresIn=URL_TTL_S,
    )
    log("job_created", namespace=ident.namespace, job_id=job_id)
    return response(202, {"jobId": job_id, "uploadUrl": url, "expiresIn": URL_TTL_S,
                          "uploadHeaders": {"Content-Type": "application/pdf"}})


def get_job(event: dict, deps: Any = None) -> dict:
    d = _deps(deps)
    ident = resolve(event, d.consumers, "jobs:read")
    job_id = path_param(event, "id")
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HttpError(404, "not_found")
    job = d.jobs(ident.namespace).get(ident.namespace, job_id)
    if job is None:
        raise HttpError(404, "not_found")
    return response(200, public_view(job))


def handler(event: dict, context: Any = None, deps: Any = None) -> dict:
    try:
        method = event.get("httpMethod", "")
        if method == "POST":
            return create_job(event, deps)
        if method == "GET":
            return get_job(event, deps)
        raise HttpError(405, "method_not_allowed")
    except HttpError as e:
        return error_response(e)
