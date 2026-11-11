# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import os
import tempfile
from typing import Any

from docclass.core.types import DocumentText, PageText
from service.common.logs import log
from service.common.settings import settings
from service.common.storage import exists, ocr_input_key, partial_key, put_json, text_key
from service.text.pdf import inspect_pdf, subset_pdf
from service.workflow.context import workflow_context


def handler(event: dict, context: Any = None, *, s3: Any = None, jobs: Any = None, bucket: str | None = None,
            max_pages: int | None = None, min_chars: int | None = None) -> dict:
    s = settings()
    max_pages = max_pages or s.max_pages
    min_chars = s.min_chars if min_chars is None else min_chars
    ns, job_id, key, doc_hash = event["namespace"], event["job_id"], event["key"], event["doc_hash"]
    ctx = workflow_context(ns, s3=s3, jobs=jobs, bucket=bucket)
    tkey = text_key(ns, doc_hash)
    if exists(ctx.s3, ctx.bucket, tkey):
        log("text_reused", namespace=ns, job_id=job_id, doc_hash=doc_hash)
        return {"needs_ocr": False, "text_key": tkey, "reused": True}
    fd, path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as f:
            for chunk in ctx.s3.get_object(Bucket=ctx.bucket, Key=key)["Body"].iter_chunks(1024 * 1024):
                f.write(chunk)
        info = inspect_pdf(path, max_pages=max_pages, min_chars=min_chars)
        pages = [{"number": n, "text": t, "source": "text_layer"} for n, t in info.pages]
        if not info.needs_ocr:
            doc = DocumentText(doc_hash, tuple(PageText(p["number"], p["text"], "text_layer") for p in pages))
            put_json(ctx.s3, ctx.bucket, tkey, doc.to_dict())
            log("text_layer", namespace=ns, job_id=job_id, doc_hash=doc_hash, pages=len(pages))
            return {"needs_ocr": False, "text_key": tkey, "reused": False}
        okey = ocr_input_key(ns, doc_hash)
        ctx.s3.put_object(Bucket=ctx.bucket, Key=okey, Body=subset_pdf(path, info.needs_ocr),
                          ContentType="application/pdf")
        put_json(ctx.s3, ctx.bucket, partial_key(ns, doc_hash), {"doc_hash": doc_hash, "pages": pages})
        log("needs_ocr", namespace=ns, job_id=job_id, doc_hash=doc_hash, pages=len(info.needs_ocr))
        return {"needs_ocr": True, "text_key": tkey, "ocr_key": okey, "ocr_pages": info.needs_ocr, "reused": False}
    finally:
        if os.path.exists(path):
            os.unlink(path)
