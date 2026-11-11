# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Key

TERMINAL = ("Completed", "NeedsReview", "Rejected", "Failed")
STATUSES = ("Pending", "AcquiringText", "Classifying") + TERMINAL
GSI = "by_hash"


def job_pk(namespace: str, job_id: str) -> str:
    return f"ns#{namespace}#job#{job_id}"


def hash_pk(namespace: str, doc_hash: str) -> str:
    return f"ns#{namespace}#hash#{doc_hash}"


def _now() -> int:
    return int(time.time())


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


class JobStore:
    def __init__(self, table: Any):
        self.table = table

    def create(self, namespace: str, job_id: str) -> dict:
        item = {"pk": job_pk(namespace, job_id), "namespace": namespace, "job_id": job_id, "status": "Pending",
                "created_at": _now(), "updated_at": _now()}
        self.table.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
        return item

    def get(self, namespace: str, job_id: str) -> dict | None:
        item = self.table.get_item(Key={"pk": job_pk(namespace, job_id)}, ConsistentRead=True).get("Item")
        if not item or item.get("namespace") != namespace:
            return None
        out = _plain(dict(item))
        if "result" in out:
            out["result"] = json.loads(out["result"])
        return out

    def set_status(self, namespace: str, job_id: str, status: str, **fields: Any) -> None:
        if status not in STATUSES:
            raise ValueError(status)
        names = {"#s": "status", "#u": "updated_at"}
        values: dict[str, Any] = {":s": status, ":u": _now()}
        sets = ["#s = :s", "#u = :u"]
        for i, (k, v) in enumerate(fields.items()):
            names[f"#f{i}"] = k
            values[f":f{i}"] = v
            sets.append(f"#f{i} = :f{i}")
        self.table.update_item(Key={"pk": job_pk(namespace, job_id)}, UpdateExpression="SET " + ", ".join(sets),
                               ExpressionAttributeNames=names, ExpressionAttributeValues=values,
                               ConditionExpression="attribute_exists(pk)")

    def complete(self, namespace: str, job_id: str, result: dict, *, doc_hash: str, config_version: str) -> str:
        status = "Completed" if result["decision"]["accepted"] else "NeedsReview"
        self.set_status(namespace, job_id, status, result=json.dumps(result, separators=(",", ":")),
                        doc_hash=doc_hash, config_version=config_version,
                        gsi_pk=hash_pk(namespace, doc_hash), gsi_sk=config_version)
        return status

    def find_completed(self, namespace: str, doc_hash: str, config_version: str) -> dict | None:
        resp = self.table.query(IndexName=GSI,
                                KeyConditionExpression=Key("gsi_pk").eq(hash_pk(namespace, doc_hash))
                                & Key("gsi_sk").eq(config_version))
        for item in resp.get("Items", []):
            if item.get("status") in ("Completed", "NeedsReview") and item.get("namespace") == namespace:
                out = _plain(dict(item))
                out["result"] = json.loads(out["result"])
                return out
        return None

    def put_textract_mapping(self, textract_job_id: str, mapping: dict) -> None:
        self.table.put_item(Item={"pk": f"textract#{textract_job_id}", **mapping, "expires_at": _now() + 86400})

    def pop_textract_mapping(self, textract_job_id: str) -> dict | None:
        item = self.table.get_item(Key={"pk": f"textract#{textract_job_id}"}, ConsistentRead=True).get("Item")
        if item:
            self.table.delete_item(Key={"pk": f"textract#{textract_job_id}"})
        return _plain(dict(item)) if item else None


def public_view(job: dict) -> dict:
    out = {"jobId": job["job_id"], "status": job["status"], "createdAt": job.get("created_at"),
           "updatedAt": job.get("updated_at")}
    if job.get("doc_hash"):
        out["docHash"] = job["doc_hash"]
    if job["status"] in ("Completed", "NeedsReview") and "result" in job:
        out["result"] = job["result"]
    if job["status"] in ("Rejected", "Failed"):
        out["reason"] = job.get("reason")
    return out


def event_detail(namespace: str, job_id: str, result: dict) -> dict:
    d = result["decision"]
    return {"detail_type": "ClassificationCompleted" if d["accepted"] else "NeedsReview",
            "detail": {"namespace": namespace, "jobId": job_id, "doc_hash": result["doc_hash"],
                       "class_id": d["class_id"], "confidence": d["confidence"], "method": d["method"],
                       "model_version": d["model_version"]}}


def failure_detail(namespace: str, job_id: str, status: str, reason: str) -> dict:
    return {"detail_type": "ClassificationFailed",
            "detail": {"namespace": namespace, "jobId": job_id, "status": status, "reason": reason}}
