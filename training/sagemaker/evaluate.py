# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

TIERS = ("sagemaker", "char_ngram_lr", "titan_nn")


def _accepts(probs: Mapping[str, float], tau: float, delta: float) -> str | None:
    if not probs:
        return None
    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    top, p = ranked[0]
    p2 = ranked[1][1] if len(ranked) > 1 else 0.0
    if top == "OTHER" or p < tau or p - p2 < delta:
        return None
    return top


def class_metrics(truth: Sequence[str], predictions: Sequence[Mapping[str, float]], cls: str,
                  tau: float, delta: float) -> dict[str, float | int]:
    relevant = [i for i, t in enumerate(truth) if t == cls]
    accepted = [i for i, p in enumerate(predictions) if _accepts(p, tau, delta) == cls]
    correct = sum(1 for i in accepted if truth[i] == cls)
    covered = sum(1 for i in relevant if _accepts(predictions[i], tau, delta) == cls)
    return {
        "support": len(relevant),
        "accepted": len(accepted),
        "precision": correct / len(accepted) if accepted else 0.0,
        "coverage": covered / len(relevant) if relevant else 0.0,
    }


def build_report(model_version: str, truth: Sequence[str], predictions: Mapping[str, Sequence[Mapping[str, float]]],
                 enabled_for: Iterable[str], thresholds: Mapping[str, Mapping[str, float]]) -> dict:
    missing = [t for t in TIERS if t not in predictions]
    if missing:
        raise ValueError(f"report needs predictions from {missing}")
    for t, preds in predictions.items():
        if len(preds) != len(truth):
            raise ValueError(f"{t}: predictions do not cover the held-out set")
    classes: dict[str, dict] = {}
    for cls in sorted(set(enabled_for)):
        classes[cls] = {
            t: class_metrics(truth, predictions[t], cls, thresholds[t]["tau"], thresholds[t]["delta"]) for t in TIERS
        }
    return {
        "model_version": model_version,
        "held_out_size": len(truth),
        "proposed_tau": thresholds["sagemaker"]["tau"],
        "proposed_delta": thresholds["sagemaker"]["delta"],
        "classes": classes,
    }
