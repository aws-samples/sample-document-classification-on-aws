# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MIN_VALIDATION = 20


@dataclass(frozen=True)
class Calibration:
    x: np.ndarray
    y: np.ndarray

    @classmethod
    def identity(cls) -> Calibration:
        return cls(np.array([0.0, 1.0]), np.array([0.0, 1.0]))

    def __call__(self, raw: np.ndarray | float) -> np.ndarray | float:
        out = np.clip(np.interp(raw, self.x, self.y), 0.0, 1.0)
        return float(out) if np.ndim(out) == 0 else out


def fit_isotonic(scores: np.ndarray, correct: np.ndarray) -> Calibration:
    scores = np.asarray(scores, dtype=np.float64)
    correct = np.asarray(correct, dtype=np.float64)
    if scores.size == 0:
        return Calibration.identity()
    order = np.argsort(scores, kind="mergesort")
    xs, ys = scores[order], correct[order]
    values: list[float] = []
    weights: list[float] = []
    lo: list[float] = []
    hi: list[float] = []
    for x, y in zip(xs, ys):
        values.append(float(y))
        weights.append(1.0)
        lo.append(float(x))
        hi.append(float(x))
        while len(values) > 1 and values[-2] > values[-1]:
            w = weights[-2] + weights[-1]
            v = (values[-2] * weights[-2] + values[-1] * weights[-1]) / w
            values[-2:] = [v]
            weights[-2:] = [w]
            hi[-2:] = [hi[-1]]
            lo[-2:] = [lo[-2]]
    bx: list[float] = []
    by: list[float] = []
    for v, a, b in zip(values, lo, hi):
        if bx and a <= bx[-1]:
            a = np.nextafter(bx[-1], np.inf)
        bx.append(a)
        by.append(v)
        if b > a:
            bx.append(b)
            by.append(v)
    if bx[0] > 0.0:
        bx.insert(0, 0.0)
        by.insert(0, 0.0)
    return Calibration(np.asarray(bx), np.clip(np.asarray(by), 0.0, 1.0))


def sweep_tau(confidence: np.ndarray, margin: np.ndarray, correct: np.ndarray, delta: float,
              target_precision: float) -> float | None:
    confidence = np.asarray(confidence, dtype=np.float64)
    margin = np.asarray(margin, dtype=np.float64)
    correct = np.asarray(correct, dtype=bool)
    for tau in np.round(np.arange(0.50, 0.995, 0.01), 2):
        accepted = (confidence >= tau) & (margin >= delta)
        n = int(accepted.sum())
        if n and correct[accepted].sum() / n >= target_precision:
            return float(tau)
    return None
