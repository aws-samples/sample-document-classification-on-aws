# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import io
import json
import tarfile
from typing import Any

from service.common.logs import log
from service.common.settings import settings
from service.common.storage import artefact_key


class ReportMissing(Exception):
    pass


def _split_s3(uri: str) -> tuple[str, str]:
    rest = uri.removeprefix("s3://")
    bucket, _, key = rest.partition("/")
    return bucket, key


def _from_tarball(data: bytes) -> tuple[bytes, dict | None]:
    artefact, report = None, None
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            name = m.name.rsplit("/", 1)[-1]
            if name == "artefact.npz":
                artefact = tar.extractfile(m).read()
            elif name == "report.json":
                report = json.loads(tar.extractfile(m).read())
    if artefact is None:
        raise ValueError("model archive has no artefact.npz")
    return artefact, report


def artefact_version(artefact: bytes) -> str:
    import numpy as np
    with np.load(io.BytesIO(artefact), allow_pickle=False) as z:
        meta = json.loads(str(z["meta"]))
    version = str(meta.get("model_version") or "")
    if not version or "/" in version:
        raise ValueError("artefact has no usable model_version")
    return version


def _thresholds(report: dict) -> dict:
    t = report.get("thresholds") or {}
    return {k: str(v) for k, v in t.items() if isinstance(v, (int, float))}


def ensure_group(sm: Any, group: str) -> None:
    try:
        sm.describe_model_package_group(ModelPackageGroupName=group)
    except sm.exceptions.ClientError:
        sm.create_model_package_group(ModelPackageGroupName=group,
                                      ModelPackageGroupDescription=f"docclass artefacts {group}")


def handler(event: dict, context: Any = None, *, s3: Any = None, sm: Any = None, bucket: str | None = None) -> dict:
    s = settings()
    bucket = bucket or s.artefact_bucket
    if s3 is None or sm is None:
        import boto3
        from service.common.aws import config
        s3 = s3 or boto3.client("s3", region_name=s.region, config=config(10, 3))
        sm = sm or boto3.client("sagemaker", region_name=s.region, config=config(10, 3))
    ns, tier = event["namespace"], event["tier"]
    if tier == "ngram":
        src_bucket, src_key = _split_s3(event["model_artifacts"])
        if not src_key.startswith(f"training/{ns}/"):
            raise ValueError("model artefacts outside namespace prefix")
        artefact, report = _from_tarball(s3.get_object(Bucket=src_bucket, Key=src_key)["Body"].read())
        if report is None:
            raise ReportMissing("training output has no report.json")
        version = artefact_version(artefact)
        akey = artefact_key(ns, "ngram", version)
        s3.put_object(Bucket=bucket, Key=akey, Body=artefact)
        rkey = akey.removesuffix(".npz") + ".report.json"
        s3.put_object(Bucket=bucket, Key=rkey, Body=json.dumps(report, default=str).encode("utf-8"),
                      ContentType="application/json")
    elif tier == "titan":
        akey, rkey, version = event["artefact_key"], event.get("report_key"), event["model_version"]
        if not akey.startswith(f"artefacts/{ns}/titan/"):
            raise ValueError("artefact outside namespace prefix")
        if not rkey:
            raise ReportMissing("index build produced no report")
        try:
            report = json.loads(s3.get_object(Bucket=bucket, Key=rkey)["Body"].read())
        except s3.exceptions.NoSuchKey:
            raise ReportMissing("index report not found")
    else:
        raise ValueError(f"unknown tier {tier}")
    group = f"{ns}-{tier}"
    ensure_group(sm, group)
    report_uri = f"s3://{bucket}/{rkey}"
    meta = {"namespace": ns, "tier": tier, "model_version": version, "artefact_key": akey, "report_uri": report_uri,
            **{f"threshold_{k}": v for k, v in _thresholds(report).items()}}
    pkg = sm.create_model_package(
        ModelPackageGroupName=group,
        ModelPackageDescription=f"{tier} {version}",
        ModelApprovalStatus="PendingManualApproval",
        CustomerMetadataProperties=meta,
        ModelMetrics={"ModelQuality": {"Statistics": {"ContentType": "application/json", "S3Uri": report_uri}}},
    )
    log("registered", namespace=ns, tier=tier, model_version=version)
    return {"model_package_arn": pkg["ModelPackageArn"], "model_version": version, "artefact_key": akey}
