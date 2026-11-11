# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

OTHER = "OTHER"


class InvalidResponse(Exception):
    pass


class VersionMismatch(Exception):
    pass


@dataclass(frozen=True)
class EndpointRequest:
    body: bytes
    truncated: bool


@dataclass(frozen=True)
class EndpointResponse:
    probabilities: Mapping[str, float]
    model_version: str


def _encode(text: str, pages: list[int], model_version: str | None) -> bytes:
    doc: dict[str, Any] = {"text": text, "pages": pages}
    if model_version:
        doc["model_version"] = model_version
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def build_request(text: str, pages: list[int], model_version: str | None = None,
                  max_payload_bytes: int = 4_000_000) -> EndpointRequest:
    body = _encode(text, pages, model_version)
    if len(body) <= max_payload_bytes:
        return EndpointRequest(body, False)
    overhead = len(_encode("", pages, model_version))
    if overhead > max_payload_bytes:
        raise ValueError("max_payload_bytes is smaller than an empty request")
    lo, hi = 0, min(len(text), max_payload_bytes - overhead)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(_encode(text[:mid], pages, model_version)) <= max_payload_bytes:
            lo = mid
        else:
            hi = mid - 1
    return EndpointRequest(_encode(text[:lo], pages, model_version), True)


def parse_response(raw: bytes, taxonomy: frozenset[str], pinned_version: str | None = None) -> EndpointResponse:
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise InvalidResponse("not json") from e
    if not isinstance(doc, dict):
        raise InvalidResponse("not an object")
    probs = doc.get("probabilities")
    version = doc.get("model_version")
    if not isinstance(probs, dict) or not probs or not isinstance(version, str) or not version:
        raise InvalidResponse("missing probabilities or model_version")
    clean: dict[str, float] = {}
    for k, v in probs.items():
        if not isinstance(k, str) or isinstance(v, bool) or not isinstance(v, (int, float)):
            raise InvalidResponse("probabilities must map class ids to numbers")
        if math.isnan(v) or v < 0:
            raise InvalidResponse("negative probability")
        if k not in taxonomy and k != OTHER:
            raise InvalidResponse(f"unknown class {k}")
        clean[k] = float(v)
    if abs(sum(clean.values()) - 1.0) > 1e-3:
        raise InvalidResponse("probabilities do not sum to 1")
    if pinned_version and version != pinned_version:
        raise VersionMismatch(version)
    return EndpointResponse(clean, version)
