# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import copy
import json
from typing import Any, Mapping, Protocol

SERVERLESS_MEMORY_MB = (1024, 2048, 3072, 4096, 5120, 6144)


class PromotionRefused(Exception):
    pass


class ModelRegistry(Protocol):
    def evaluation_report(self, model_version: str) -> Mapping[str, Any] | None: ...


class SageMakerModelRegistry:
    def __init__(self, package_group: str, sagemaker_client: Any = None, s3_client: Any = None):
        if sagemaker_client is None:
            import boto3
            sagemaker_client = boto3.client("sagemaker")
        self.group = package_group
        self.sm = sagemaker_client
        self._s3 = s3_client

    def _s3_client(self) -> Any:
        if self._s3 is None:
            import boto3
            self._s3 = boto3.client("s3")
        return self._s3

    def _read_uri(self, uri: str) -> Mapping[str, Any] | None:
        if not uri.startswith("s3://"):
            return None
        bucket, _, key = uri[5:].partition("/")
        body = self._s3_client().get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(body)

    def evaluation_report(self, model_version: str) -> Mapping[str, Any] | None:
        pages = self.sm.get_paginator("list_model_packages").paginate(ModelPackageGroupName=self.group)
        for page in pages:
            for summary in page.get("ModelPackageSummaryList", []):
                pkg = self.sm.describe_model_package(ModelPackageName=summary["ModelPackageArn"])
                meta = pkg.get("CustomerMetadataProperties", {})
                if meta.get("model_version") != model_version:
                    continue
                if meta.get("evaluation_report"):
                    return json.loads(meta["evaluation_report"])
                uri = meta.get("report_uri") or (
                    pkg.get("ModelMetrics", {}).get("ModelQuality", {}).get("Statistics", {}).get("S3Uri"))
                return self._read_uri(uri) if uri else None
        return None


def serverless_config(memory_mb: int | None, max_concurrency: int | None) -> dict[str, int]:
    if memory_mb is None or max_concurrency is None:
        raise ValueError("memory_mb and max_concurrency must both be set explicitly")
    if isinstance(memory_mb, bool) or memory_mb not in SERVERLESS_MEMORY_MB:
        raise ValueError(f"memory_mb must be one of {SERVERLESS_MEMORY_MB}")
    if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int) or not 1 <= max_concurrency <= 200:
        raise ValueError("max_concurrency must be an integer in 1..200")
    return {"MemorySizeInMB": memory_mb, "MaxConcurrency": max_concurrency}


def gated_classes(report: Mapping[str, Any], proposed: list[str]) -> tuple[list[str], dict[str, str]]:
    keep: list[str] = []
    dropped: dict[str, str] = {}
    for cls in proposed:
        metrics = report.get("classes", {}).get(cls)
        if not metrics:
            dropped[cls] = "not evaluated"
            continue
        mine = metrics["sagemaker"]["precision"]
        best_other = max(metrics["char_ngram_lr"]["precision"], metrics["titan_nn"]["precision"])
        if metrics["sagemaker"]["accepted"] == 0 or mine <= best_other:
            dropped[cls] = f"precision {mine:.4f} does not beat {best_other:.4f}"
        else:
            keep.append(cls)
    return keep, dropped


def promote(model_version: str, registry: ModelRegistry, current_config: Mapping[str, Any],
            proposed_enabled_for: list[str], tier_name: str = "sagemaker") -> dict[str, Any]:
    report = registry.evaluation_report(model_version)
    if not report:
        raise PromotionRefused(f"model {model_version} has no evaluation report")
    if report.get("model_version") != model_version:
        raise PromotionRefused("evaluation report belongs to a different model version")
    keep, dropped = gated_classes(report, proposed_enabled_for)
    proposal = copy.deepcopy(dict(current_config))
    tiers = proposal.setdefault("tiers", [])
    entry = next((t for t in tiers if t.get("name") == tier_name), None)
    if entry is None:
        entry = {"name": tier_name}
        tiers.append(entry)
    entry["model_version"] = model_version
    entry["enabled_for"] = keep
    entry["enabled"] = bool(keep)
    entry["thresholds"] = {"tau": report["proposed_tau"], "delta": report["proposed_delta"]}
    return {"status": "proposed", "config": proposal, "dropped": dropped, "model_version": model_version}
