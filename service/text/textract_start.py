# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import hashlib
import re
from typing import Any

from service.common.aws import valid_namespace
from service.common.logs import log
from service.common.settings import settings
from service.common.storage import ocr_input_key


def _tag(job_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.\-:]", "-", job_id)[:64]


def handler(event: dict, context: Any = None, *, textract: Any = None, mapping_jobs: Any = None,
            bucket: str | None = None) -> dict:
    s = settings()
    token, inp = event["token"], event["input"]
    ns, job_id, doc_hash = inp["namespace"], inp["job_id"], inp["doc_hash"]
    if not valid_namespace(ns):
        raise ValueError("invalid namespace")
    bucket = bucket or s.data_bucket
    if inp["text"]["ocr_key"] != ocr_input_key(ns, doc_hash):
        raise ValueError("ocr input does not belong to namespace")
    if textract is None or mapping_jobs is None:
        import boto3
        from service.common.aws import config
        from service.common.jobs import JobStore
        textract = textract or boto3.client("textract", region_name=s.region, config=config(10, 3))
        mapping_jobs = mapping_jobs or JobStore(boto3.resource("dynamodb", region_name=s.region, config=config(2, 3))
                                                .Table(s.jobs_table))
    resp = textract.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": bucket, "Name": inp["text"]["ocr_key"]}},
        ClientRequestToken=hashlib.sha256(f"{ns}:{job_id}".encode("utf-8")).hexdigest()[:64],
        JobTag=_tag(job_id),
        NotificationChannel={"SNSTopicArn": s.textract_topic_arn, "RoleArn": s.textract_role_arn},
    )
    tj = resp["JobId"]
    mapping_jobs.put_textract_mapping(tj, {"namespace": ns, "job_id": job_id, "doc_hash": doc_hash, "token": token,
                                      "ocr_pages": [int(p) for p in inp["text"]["ocr_pages"]]})
    log("textract_started", namespace=ns, job_id=job_id, doc_hash=doc_hash, textract_job_id=tj)
    return {"textract_job_id": tj}
