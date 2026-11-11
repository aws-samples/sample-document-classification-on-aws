# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
import sys
import time
from typing import Any, Mapping

from docclass.core.types import UNKNOWN, CascadeResult


def _reason_kind(reason: str | None) -> str:
    if not reason:
        return "none"
    return reason.split(":", 1)[0].strip().replace(" ", "_")[:40]


def _doc(metric_ns: str, dims: list[list[str]], metrics: Mapping[str, tuple[float, str]], values: Mapping[str, Any]) -> dict:
    doc: dict[str, Any] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": metric_ns,
                "Dimensions": dims,
                "Metrics": [{"Name": n, "Unit": u} for n, (_, u) in metrics.items()],
            }],
        },
    }
    doc.update(values)
    doc.update({n: v for n, (v, _) in metrics.items()})
    return doc


def estimated_cost(result: CascadeResult, prices: Mapping[str, float], ocr_pages: int = 0) -> float:
    cost = ocr_pages * float(prices.get("textract_page", 0.0))
    for a in result.attempts:
        if a.error is not None:
            continue
        cost += float(prices.get(f"tier.{a.tier}", 0.0))
    return round(cost, 8)


def documents(result: CascadeResult, namespace: str, metric_ns: str, prices: Mapping[str, float],
              ocr_pages: int = 0) -> list[dict]:
    docs = []
    for a in result.attempts:
        accepted = a.decision.accepted and a.decision.class_id != UNKNOWN
        reason = a.decision.reason or ""
        throttled = 1.0 if "throttl" in reason else 0.0
        docs.append(_doc(metric_ns, [["namespace", "tier"], ["tier"]], {
            "Attempts": (1.0, "Count"),
            "Accepted": (1.0 if accepted else 0.0, "Count"),
            "Abstained": (0.0 if accepted else 1.0, "Count"),
            "Throttled": (throttled, "Count"),
            "LatencyMs": (round(a.elapsed_ms, 3), "Milliseconds"),
        }, {"namespace": namespace, "tier": a.tier}))
        if not accepted:
            docs.append(_doc(metric_ns, [["namespace", "tier", "reason"]], {"Abstained": (1.0, "Count")},
                             {"namespace": namespace, "tier": a.tier, "reason": _reason_kind(reason)}))
    unknown = 0.0 if result.decision.accepted else 1.0
    summary = {
        "UnknownRate": (unknown, "None"),
        "EstimatedCostUSD": (estimated_cost(result, prices, ocr_pages), "None"),
    }
    if result.decision.accepted:
        summary["MeanAcceptedConfidence"] = (result.decision.confidence, "None")
    docs.append(_doc(metric_ns, [["namespace"], []], summary, {"namespace": namespace}))
    return docs


def emit(result: CascadeResult, namespace: str, metric_ns: str, prices: Mapping[str, float], ocr_pages: int = 0,
         stream: Any = None) -> list[dict]:
    docs = documents(result, namespace, metric_ns, prices, ocr_pages)
    out = stream or sys.stdout
    for d in docs:
        print(json.dumps(d, separators=(",", ":")), file=out)
    return docs
