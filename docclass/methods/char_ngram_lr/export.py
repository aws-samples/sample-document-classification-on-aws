# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import numpy as np

from docclass.methods.char_ngram_lr.train import TrainedModel


def _calibration(model: TrainedModel) -> tuple[np.ndarray, np.ndarray]:
    if model.calibrator is None:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
    calibrators = model.calibrator.calibrated_classifiers_[0].calibrators
    return (np.array([float(c.a_) for c in calibrators], dtype=np.float64),
            np.array([float(c.b_) for c in calibrators], dtype=np.float64))


def artefact_arrays(model: TrainedModel) -> dict[str, np.ndarray]:
    vocab = model.vectorizer.vocabulary_
    keys = sorted(vocab)
    calib_a, calib_b = _calibration(model)
    c = model.centroids
    return {
        "vocab_keys": np.array(keys, dtype=np.str_),
        "vocab_idx": np.array([vocab[k] for k in keys], dtype=np.int32),
        "idf": np.asarray(model.vectorizer.idf_, dtype=np.float64),
        "coef": np.asarray(model.lr.coef_, dtype=np.float64),
        "intercept": np.asarray(model.lr.intercept_, dtype=np.float64),
        "calib_a": calib_a,
        "calib_b": calib_b,
        "cent_data": np.asarray(c.data, dtype=np.float64),
        "cent_indices": np.asarray(c.indices, dtype=np.int32),
        "cent_indptr": np.asarray(c.indptr, dtype=np.int64),
    }


def content_version(arrays: dict[str, np.ndarray]) -> str:
    h = hashlib.sha256()
    for k in sorted(arrays):
        h.update(k.encode())
        h.update(np.ascontiguousarray(arrays[k]).tobytes())
    return "ngram-" + h.hexdigest()[:12]


def write_artefact(arrays: dict[str, np.ndarray], meta: dict[str, Any], target: str | Path | None = None) -> bytes:
    buf = io.BytesIO()
    np.savez(buf, meta=np.array(json.dumps(meta, sort_keys=True)), **arrays)
    data = buf.getvalue()
    if target is not None:
        Path(target).write_bytes(data)
    return data


def export(model: TrainedModel, target: str | Path | None = None) -> bytes:
    arrays = artefact_arrays(model)
    meta = {
        "model_version": model.config.model_version or content_version(arrays),
        "taxonomy_version": model.config.taxonomy_version,
        "ngram_range": list(model.config.ngram_range),
        "max_pages": model.config.max_pages,
        "thresholds": model.thresholds,
        "enabled": model.enabled,
        "calibrated": model.calibrator is not None,
        "classes": list(model.classes),
        "report": model.report,
    }
    return write_artefact(arrays, meta, target)
