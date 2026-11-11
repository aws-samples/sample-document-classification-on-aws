# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import io
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

from docclass.methods.char_ngram_lr.preprocess import char_wb_ngrams

ArtefactSource = bytes | str | Path | BinaryIO


@dataclass(frozen=True)
class Vector:
    indices: np.ndarray
    values: np.ndarray


def _expit(x: np.ndarray) -> np.ndarray:
    out = np.empty_like(x, dtype=np.float64)
    pos = x >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
    ex = np.exp(x[~pos])
    out[~pos] = ex / (1.0 + ex)
    return out


class Scorer:
    def __init__(self, meta: dict[str, Any], vocab: dict[str, int], idf: np.ndarray, coef: np.ndarray,
                 intercept: np.ndarray, calib_a: np.ndarray, calib_b: np.ndarray, cent_data: np.ndarray,
                 cent_indices: np.ndarray, cent_indptr: np.ndarray):
        self.meta = meta
        self.vocab = vocab
        self.idf = idf
        self.coef = coef
        self.intercept = intercept
        self.calib_a = calib_a
        self.calib_b = calib_b
        self.cent_data = cent_data
        self.cent_indices = cent_indices
        self.cent_indptr = cent_indptr
        self.classes: tuple[str, ...] = tuple(meta["classes"])
        self.min_n, self.max_n = (int(v) for v in meta["ngram_range"])
        self.calibrated = bool(meta.get("calibrated", False))

    @property
    def model_version(self) -> str:
        return str(self.meta["model_version"])

    @property
    def taxonomy_version(self) -> str:
        return str(self.meta["taxonomy_version"])

    @property
    def enabled(self) -> bool:
        return bool(self.meta.get("enabled", False))

    @property
    def thresholds(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.meta["thresholds"].items()}

    @property
    def max_pages(self) -> int:
        return int(self.meta.get("max_pages", 2))

    @classmethod
    def load(cls, source: ArtefactSource) -> Scorer:
        if isinstance(source, (bytes, bytearray)):
            source = io.BytesIO(source)
        with np.load(source, allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
            keys = z["vocab_keys"]
            idx = z["vocab_idx"]
            vocab = dict(zip(keys.tolist(), idx.tolist()))
            return cls(meta, vocab, z["idf"].astype(np.float64), z["coef"].astype(np.float64),
                       z["intercept"].astype(np.float64), z["calib_a"].astype(np.float64),
                       z["calib_b"].astype(np.float64), z["cent_data"].astype(np.float64),
                       z["cent_indices"].astype(np.int64), z["cent_indptr"].astype(np.int64))

    def vectorize(self, text: str) -> Vector:
        counts = Counter(self.vocab[g] for g in char_wb_ngrams(text, self.min_n, self.max_n) if g in self.vocab)
        if not counts:
            return Vector(np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float64))
        indices = np.fromiter(counts.keys(), dtype=np.int64, count=len(counts))
        tf = np.fromiter(counts.values(), dtype=np.float64, count=len(counts))
        values = (np.log(tf) + 1.0) * self.idf[indices]
        norm = math.sqrt(float(values @ values))
        if norm > 0:
            values = values / norm
        order = np.argsort(indices)
        return Vector(indices[order], values[order])

    def decision_function(self, x: Vector) -> np.ndarray:
        return self.coef[:, x.indices] @ x.values + self.intercept

    def proba_from_vector(self, x: Vector) -> np.ndarray:
        z = self.decision_function(x)
        if self.calibrated:
            p = _expit(-(self.calib_a * z + self.calib_b))
            if len(self.classes) == 2:
                return np.array([1.0 - p[0], p[0]])
            total = p.sum()
            if total == 0:
                return np.full(len(self.classes), 1.0 / len(self.classes))
            return p / total
        if len(self.classes) == 2:
            p1 = _expit(z)[0]
            return np.array([1.0 - p1, p1])
        e = np.exp(z - z.max())
        return e / e.sum()

    def proba(self, text: str) -> np.ndarray:
        return self.proba_from_vector(self.vectorize(text))

    def centroid_similarity(self, class_index: int, x: Vector) -> float:
        start, end = self.cent_indptr[class_index], self.cent_indptr[class_index + 1]
        if end == start or x.indices.size == 0:
            return 0.0
        cols = self.cent_indices[start:end]
        data = self.cent_data[start:end]
        common, ci, xi = np.intersect1d(cols, x.indices, assume_unique=True, return_indices=True)
        if common.size == 0:
            return 0.0
        return float(data[ci] @ x.values[xi])
