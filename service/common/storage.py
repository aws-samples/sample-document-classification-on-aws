# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
import threading
from typing import Any

from docclass.core.types import DocumentText


def text_key(namespace: str, doc_hash: str) -> str:
    return f"text/{namespace}/{doc_hash}.json"


def partial_key(namespace: str, doc_hash: str) -> str:
    return f"text/{namespace}/{doc_hash}.partial.json"


def ocr_input_key(namespace: str, doc_hash: str) -> str:
    return f"text/{namespace}/{doc_hash}.ocr.pdf"


def inbox_key(namespace: str, job_id: str) -> str:
    return f"inbox/{namespace}/{job_id}"


def label_key(namespace: str, doc_hash: str) -> str:
    return f"labelled/{namespace}/{doc_hash}.json"


def artefact_key(namespace: str, tier: str, version: str) -> str:
    return f"artefacts/{namespace}/{tier}/{version}.npz"


def exists(s3: Any, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except s3.exceptions.ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound", "403", "AccessDenied"):
            return False
        raise


def put_json(s3: Any, bucket: str, key: str, value: Any) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(value, separators=(",", ":")).encode("utf-8"),
                  ContentType="application/json")


def get_json(s3: Any, bucket: str, key: str) -> Any:
    return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())


def get_document_text(s3: Any, bucket: str, namespace: str, doc_hash: str) -> DocumentText | None:
    if not exists(s3, bucket, text_key(namespace, doc_hash)):
        return None
    return DocumentText.from_dict(get_json(s3, bucket, text_key(namespace, doc_hash)))


class ArtefactCache:
    def __init__(self, max_items: int = 32):
        self._items: dict[tuple[str, str], bytes] = {}
        self._lock = threading.Lock()
        self.max_items = max_items
        self.fetches = 0

    def load(self, s3: Any, bucket: str, key: str) -> bytes:
        with self._lock:
            if (bucket, key) in self._items:
                return self._items[(bucket, key)]
        data = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        with self._lock:
            self.fetches += 1
            if len(self._items) >= self.max_items:
                self._items.pop(next(iter(self._items)))
            self._items[(bucket, key)] = data
        return data
