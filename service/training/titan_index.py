# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import dataclasses
import json
from typing import Any

from docclass.core.types import DocumentText
from service.common.logs import log
from service.common.settings import settings
from service.common.storage import artefact_key


def load_exemplars(s3: Any, bucket: str, namespace: str) -> list[tuple[DocumentText, str]]:
    out = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"exemplars/{namespace}/"):
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith(".json"):
                continue
            rec = json.loads(s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read())
            out.append((DocumentText.from_dict(rec["document"]), rec["class_id"]))
    return out


def split(items: list, every: int = 5) -> tuple[list, list]:
    train, val = [], []
    for i, item in enumerate(sorted(items, key=lambda x: x[0].doc_hash)):
        (val if i % every == every - 1 else train).append(item)
    return train, val


def handler(event: dict, context: Any = None, *, data_s3: Any = None, artefact_s3: Any = None, embedder: Any = None,
            data_bucket: str | None = None, artefact_bucket: str | None = None) -> dict:
    s = settings()
    ns = event["namespace"]
    if data_s3 is None or artefact_s3 is None or embedder is None:
        import boto3
        from docclass.methods.titan_nn.embedder import BedrockTitanEmbedder
        from service.common.aws import NamespaceSessions, config
        session = NamespaceSessions(s.data_access_role_arn, s.region).session(ns)
        data_s3 = data_s3 or session.client("s3", config=config(10, 3))
        artefact_s3 = artefact_s3 or session.client("s3", config=config(10, 3))
        embedder = embedder or BedrockTitanEmbedder(boto3.client("bedrock-runtime", region_name=s.region,
                                                                 config=config(5, 3)))
    from docclass.methods.titan_nn.index import build_index
    train, val = split(load_exemplars(data_s3, data_bucket or s.data_bucket, ns))
    if not train:
        raise ValueError("no exemplars")
    index, report = build_index(train, embedder, taxonomy_version=str(event["taxonomy_version"]), validation=val)
    version = index.index_version
    bucket = artefact_bucket or s.artefact_bucket
    akey = artefact_key(ns, "titan", version)
    artefact_s3.put_object(Bucket=bucket, Key=akey, Body=index.to_bytes())
    rkey = akey.removesuffix(".npz") + ".report.json"
    body = dataclasses.asdict(report)
    body["thresholds"] = {k: v for k, v in (("tau", report.tau), ("delta", report.delta)) if v is not None}
    artefact_s3.put_object(Bucket=bucket, Key=rkey, Body=json.dumps(body, default=str).encode("utf-8"),
                           ContentType="application/json")
    log("index_built", namespace=ns, model_version=version, count=len(train))
    return {"artefact_key": akey, "report_key": rkey, "model_version": version, "enabled": report.enabled}
