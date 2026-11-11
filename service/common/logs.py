# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
import sys
import time
from typing import Any

ALLOWED = frozenset({
    "event", "namespace", "job_id", "doc_hash", "class_id", "confidence", "accepted", "method", "model_version",
    "taxonomy_version", "config_version", "status", "reason", "tier", "elapsed_ms", "error", "pages", "size_bytes",
    "execution", "textract_job_id", "count",
})


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return value[:200]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:200]


def log(event: str, stream: Any = None, **fields: Any) -> dict:
    record = {"ts": round(time.time(), 3), "event": event}
    for k, v in fields.items():
        if k in ALLOWED:
            record[k] = _scrub(v)
    print(json.dumps(record, separators=(",", ":")), file=stream or sys.stdout)
    return record
