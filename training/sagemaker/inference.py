# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
import math
import os
from typing import Any, Callable, Mapping, Sequence


def softmax(logits: Sequence[float]) -> list[float]:
    m = max(logits)
    exps = [math.exp(x - m) for x in logits]
    total = sum(exps)
    return [e / total for e in exps]


def model_fn(model_dir: str) -> dict[str, Any]:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    with open(os.path.join(model_dir, "docclass.json")) as f:
        meta = json.load(f)
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)  # loads the local model directory  # nosec B615
    model = AutoModelForSequenceClassification.from_pretrained(model_dir, local_files_only=True)  # nosec B615
    model.eval()
    max_length = int(meta.get("max_length", 512))

    def score(text: str) -> list[float]:
        enc = tokenizer(text, truncation=True, max_length=max_length, return_tensors="pt")
        with torch.no_grad():
            return model(**enc).logits[0].tolist()

    return {"score": score, "labels": list(meta["labels"]), "model_version": str(meta["model_version"])}


def input_fn(body: bytes | str, content_type: str = "application/json") -> dict[str, Any]:
    if content_type != "application/json":
        raise ValueError(f"unsupported content type {content_type}")
    doc = json.loads(body)
    if not isinstance(doc, dict) or not isinstance(doc.get("text"), str):
        raise ValueError("request must be an object with text")
    return doc


def predict_fn(request: Mapping[str, Any], model: Mapping[str, Any]) -> dict[str, Any]:
    score: Callable[[str], Sequence[float]] = model["score"]
    labels: list[str] = model["labels"]
    probs = softmax(list(score(request["text"])))
    if len(probs) != len(labels):
        raise ValueError("model output does not match its labels")
    return {"probabilities": dict(zip(labels, probs)), "model_version": model["model_version"]}


def output_fn(prediction: Mapping[str, Any], accept: str = "application/json") -> str:
    return json.dumps({"probabilities": prediction["probabilities"], "model_version": prediction["model_version"]})
