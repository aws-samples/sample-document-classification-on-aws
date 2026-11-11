# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
from typing import Any

from docclass.core.types import DocumentText, PageText
from service.common.logs import log
from service.common.settings import settings
from service.common.storage import get_json, partial_key, put_json, text_key
from service.workflow.context import workflow_context


def ocr_lines(textract: Any, job_id: str) -> dict[int, list[str]]:
    pages: dict[int, list[str]] = {}
    token = None
    while True:
        kw = {"JobId": job_id, "MaxResults": 1000}
        if token:
            kw["NextToken"] = token
        resp = textract.get_document_text_detection(**kw)
        for b in resp.get("Blocks", []):
            if b.get("BlockType") == "LINE":
                pages.setdefault(int(b.get("Page", 1)), []).append(b.get("Text", ""))
        token = resp.get("NextToken")
        if not token:
            return pages


def merge(partial: dict, ocr_pages: list[int], lines: dict[int, list[str]]) -> DocumentText:
    by_number = {p["number"]: dict(p) for p in partial["pages"]}
    for subset_index, original in enumerate(ocr_pages, start=1):
        by_number[original] = {"number": original, "text": "\n".join(lines.get(subset_index, [])), "source": "ocr"}
    pages = tuple(PageText(n, by_number[n]["text"], by_number[n]["source"]) for n in sorted(by_number))
    return DocumentText(partial["doc_hash"], pages)


def handler(event: dict, context: Any = None, *, textract: Any = None, states: Any = None, jobs: Any = None,
            s3: Any = None, bucket: str | None = None, mapping_jobs: Any = None) -> dict:
    s = settings()
    if textract is None or states is None or mapping_jobs is None:
        import boto3
        from service.common.aws import config
        from service.common.jobs import JobStore
        textract = textract or boto3.client("textract", region_name=s.region, config=config(10, 3))
        states = states or boto3.client("stepfunctions", region_name=s.region, config=config(5, 3))
        mapping_jobs = mapping_jobs or JobStore(boto3.resource("dynamodb", region_name=s.region, config=config(2, 3))
                                                .Table(s.jobs_table))
    handled = 0
    for record in event.get("Records", []):
        msg = json.loads(record["Sns"]["Message"])
        mapping = mapping_jobs.pop_textract_mapping(msg["JobId"])
        if not mapping:
            log("textract_unknown_job", textract_job_id=msg.get("JobId"))
            continue
        ns, job_id, doc_hash, token = mapping["namespace"], mapping["job_id"], mapping["doc_hash"], mapping["token"]
        if msg.get("Status") != "SUCCEEDED":
            states.send_task_failure(taskToken=token, error="TextractFailed", cause=str(msg.get("Status"))[:200])
            log("textract_failed", namespace=ns, job_id=job_id, status=msg.get("Status"))
            handled += 1
            continue
        try:
            ctx = workflow_context(ns, s3=s3, jobs=jobs, bucket=bucket)
            partial = get_json(ctx.s3, ctx.bucket, partial_key(ns, doc_hash))
            doc = merge(partial, [int(p) for p in mapping["ocr_pages"]], ocr_lines(textract, msg["JobId"]))
            put_json(ctx.s3, ctx.bucket, text_key(ns, doc_hash), doc.to_dict())
        except Exception as e:
            states.send_task_failure(taskToken=token, error="OcrMergeFailed", cause=type(e).__name__)
            log("ocr_merge_failed", namespace=ns, job_id=job_id, error=type(e).__name__)
            handled += 1
            continue
        states.send_task_success(taskToken=token, output=json.dumps({"text_key": text_key(ns, doc_hash)}))
        log("textract_merged", namespace=ns, job_id=job_id, doc_hash=doc_hash, pages=len(doc.pages))
        handled += 1
    return {"handled": handled}
