# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import base64
import json
from typing import Any


class HttpError(Exception):
    def __init__(self, status: int, error: str, **extra: Any):
        super().__init__(error)
        self.status = status
        self.body = {"error": error, **extra}


def response(status: int, body: Any) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body, separators=(",", ":"), default=str),
    }


def error_response(e: HttpError) -> dict:
    return response(e.status, e.body)


def json_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HttpError(400, "invalid_json")
    if not isinstance(body, dict):
        raise HttpError(400, "invalid_json")
    return body


def path_param(event: dict, name: str) -> str:
    value = (event.get("pathParameters") or {}).get(name)
    if not value:
        raise HttpError(400, "missing_path_parameter", name=name)
    return value
