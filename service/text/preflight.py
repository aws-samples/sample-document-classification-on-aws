# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from typing import Any

from service.common.logs import log
from service.text.pdf import MAX_BYTES, PdfError, preflight
from service.workflow.context import workflow_context


def _consumer_limit(namespace: str, consumers: Any) -> int:
    if consumers is None:
        import boto3
        from service.common.aws import config
        from service.common.settings import settings
        s = settings()
        consumers = boto3.resource("dynamodb", region_name=s.region, config=config(2, 3)).Table(s.consumers_table)
    item = consumers.get_item(Key={"pk": f"ns#{namespace}#consumer"}).get("Item") or {}
    return int(item.get("max_concurrent_jobs", 10))


def handler(event: dict, context: Any = None, *, s3: Any = None, jobs: Any = None, bucket: str | None = None,
            consumers: Any = None) -> dict:
    ns, job_id, key = event["namespace"], event["job_id"], event["key"]
    ctx = workflow_context(ns, s3=s3, jobs=jobs, bucket=bucket)
    if key != f"inbox/{ns}/{job_id}":
        raise ValueError("key does not belong to job")
    head = ctx.s3.head_object(Bucket=ctx.bucket, Key=key)
    size = int(head["ContentLength"])
    limit = _consumer_limit(ns, consumers)
    if size > MAX_BYTES:
        reason = f"file exceeds {MAX_BYTES} bytes"
        ctx.jobs.set_status(ns, job_id, "Rejected", reason=reason)
        return {"status": "rejected", "reason": reason, "max_concurrent_jobs": limit}
    digest = hashlib.sha256()
    fd, path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as f:
            for chunk in ctx.s3.get_object(Bucket=ctx.bucket, Key=key)["Body"].iter_chunks(1024 * 1024):
                digest.update(chunk)
                f.write(chunk)
        doc_hash = digest.hexdigest()
        try:
            pages = preflight(path, size)
        except PdfError as e:
            ctx.jobs.set_status(ns, job_id, "Rejected", reason=str(e), doc_hash=doc_hash)
            log("rejected", namespace=ns, job_id=job_id, doc_hash=doc_hash, reason=str(e))
            return {"status": "rejected", "reason": str(e), "doc_hash": doc_hash, "max_concurrent_jobs": limit}
    finally:
        if os.path.exists(path):
            os.unlink(path)
    ctx.jobs.set_status(ns, job_id, "AcquiringText", doc_hash=doc_hash, page_count=pages, size_bytes=size)
    log("preflight", namespace=ns, job_id=job_id, doc_hash=doc_hash, pages=pages, size_bytes=size)
    return {"status": "ok", "doc_hash": doc_hash, "page_count": pages, "size_bytes": size, "max_concurrent_jobs": limit,
            "lease_expires_at": int(time.time()) + 3600}
