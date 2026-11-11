# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from docclass.core import DocumentText
from docclass.methods.titan_nn.calibrate import MIN_VALIDATION, Calibration, fit_isotonic, sweep_tau
from docclass.methods.titan_nn.embedder import Embedder, unit
from docclass.text import prepare


def prepared_text(doc: DocumentText, max_pages: int = 2, max_input_chars: int = 20_000) -> str:
    return prepare(doc, max_pages)[:max_input_chars]


@dataclass
class ClassIndex:
    meta: dict[str, Any]
    vectors: np.ndarray
    labels: np.ndarray
    centroids: np.ndarray
    calibration: Calibration
    _pad: np.ndarray = field(init=False, repr=False)
    _mask: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.vectors = np.ascontiguousarray(self.vectors, dtype=np.float32)
        self.labels = np.asarray(self.labels, dtype=np.int32)
        counts = np.bincount(self.labels, minlength=len(self.classes)) if self.labels.size else np.zeros(
            len(self.classes), dtype=np.int64)
        width = int(counts.max()) if counts.size else 0
        pad = np.zeros((len(self.classes), max(width, 1)), dtype=np.int64)
        mask = np.zeros_like(pad, dtype=bool)
        for c in range(len(self.classes)):
            idx = np.flatnonzero(self.labels == c)
            pad[c, :idx.size] = idx
            mask[c, :idx.size] = True
        self._pad, self._mask = pad, mask

    @property
    def classes(self) -> list[str]:
        return list(self.meta["classes"])

    @property
    def index_version(self) -> str:
        return str(self.meta["index_version"])

    @property
    def enabled(self) -> bool:
        return bool(self.meta["enabled"])

    def raw_scores(self, q: np.ndarray, k: int | None = None) -> np.ndarray:
        k = int(k or self.meta["k"])
        s = np.maximum(self.vectors @ np.asarray(q, dtype=np.float32), 0.0)
        grid = np.where(self._mask, s[self._pad], 0.0)
        top = -np.sort(-grid, axis=1)[:, :k]
        denom = top.sum(axis=1)
        num = (top * top).sum(axis=1)
        return np.divide(num, denom, out=np.zeros_like(num), where=denom > 0)

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        np.savez(buf, **_arrays(self.meta, self.vectors, self.labels, self.centroids, self.calibration))
        return buf.getvalue()

    def save(self, directory: str | Path) -> Path:
        path = Path(directory) / f"{self.index_version}.npz"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(self.to_bytes())
            tmp.replace(path)
        return path

    @classmethod
    def load(cls, source: str | Path | bytes) -> ClassIndex:
        data = np.load(io.BytesIO(source) if isinstance(source, bytes) else source, allow_pickle=False)
        meta = json.loads(bytes(data["meta"]).decode("utf-8"))
        return cls(meta, data["vectors"], data["labels"], data["centroids"],
                   Calibration(data["calib_x"].astype(np.float64), data["calib_y"].astype(np.float64)))


def _arrays(meta: dict[str, Any], vectors: np.ndarray, labels: np.ndarray, centroids: np.ndarray,
            calibration: Calibration) -> dict[str, np.ndarray]:
    return {
        "meta": np.frombuffer(json.dumps(meta, sort_keys=True).encode("utf-8"), dtype=np.uint8),
        "vectors": np.asarray(vectors, dtype=np.float32),
        "labels": np.asarray(labels, dtype=np.int32),
        "centroids": np.asarray(centroids, dtype=np.float32),
        "calib_x": np.asarray(calibration.x, dtype=np.float64),
        "calib_y": np.asarray(calibration.y, dtype=np.float64),
    }


def _version(meta: dict[str, Any], vectors: np.ndarray, labels: np.ndarray, centroids: np.ndarray,
             calibration: Calibration) -> str:
    h = hashlib.sha256()
    body = {k: v for k, v in meta.items() if k != "index_version"}
    for name, arr in sorted(_arrays(body, vectors, labels, centroids, calibration).items()):
        h.update(name.encode())
        h.update(np.ascontiguousarray(arr).tobytes())
    return h.hexdigest()[:12]


@dataclass
class BuildReport:
    skipped_classes: list[str]
    enabled: bool
    tau: float | None
    delta: float
    validation_size: int
    selective_precision: float | None
    coverage: float | None
    notes: list[str]


def build_index(exemplars: Iterable[tuple[DocumentText, str]], embedder: Embedder, *, taxonomy_version: str,
                validation: Sequence[tuple[DocumentText, str]] = (), target_precision: float = 0.99,
                delta: float = 0.02, k: int = 3, min_exemplars: int = 1, max_pages: int = 2,
                max_input_chars: int = 20_000) -> tuple[ClassIndex, BuildReport]:
    exemplars = list(exemplars)
    by_class: dict[str, list[np.ndarray]] = {}
    exemplar_hashes = set()
    for doc, cls in exemplars:
        exemplar_hashes.add(doc.doc_hash)
        by_class.setdefault(cls, []).append(unit(embedder.embed(prepared_text(doc, max_pages, max_input_chars))))
    for doc, _ in validation:
        if doc.doc_hash in exemplar_hashes:
            raise ValueError(f"validation document {doc.doc_hash[:12]} is also an exemplar")

    skipped = sorted(c for c, v in by_class.items() if len(v) < min_exemplars)
    classes = sorted(c for c, v in by_class.items() if len(v) >= min_exemplars)
    vectors = [v for c in classes for v in by_class[c]]
    labels = [i for i, c in enumerate(classes) for _ in by_class[c]]
    centroids = [unit(np.mean(by_class[c], axis=0)) for c in classes]
    dims = embedder.dimensions
    vec_arr = np.asarray(vectors, dtype=np.float32).reshape(-1, dims)
    lab_arr = np.asarray(labels, dtype=np.int32)
    cen_arr = np.asarray(centroids, dtype=np.float32).reshape(-1, dims)

    meta: dict[str, Any] = {
        "embedding_model_id": embedder.model_id,
        "dimensions": dims,
        "normalize": True,
        "taxonomy_version": taxonomy_version,
        "k": k,
        "tau": None,
        "delta": delta,
        "rho": 0.0,
        "enabled": False,
        "classes": classes,
        "skipped_classes": skipped,
        "max_pages": max_pages,
        "max_input_chars": max_input_chars,
    }
    calibration = Calibration.identity()
    notes: list[str] = []
    precision = coverage = None
    draft = ClassIndex(dict(meta, index_version="draft"), vec_arr, lab_arr, cen_arr, calibration)

    if len(validation) < MIN_VALIDATION:
        notes.append(f"validation set has {len(validation)} documents; at least {MIN_VALIDATION} are needed")
    elif not classes:
        notes.append("no class has enough exemplars")
    else:
        tops, margins, correct = [], [], []
        for doc, cls in validation:
            raw = draft.raw_scores(embedder.embed(prepared_text(doc, max_pages, max_input_chars)))
            order = np.argsort(-raw)
            tops.append(raw[order[0]])
            margins.append(raw[order[0]] - (raw[order[1]] if raw.size > 1 else 0.0))
            correct.append(classes[order[0]] == cls)
        calibration = fit_isotonic(np.asarray(tops), np.asarray(correct))
        conf = np.asarray(calibration(np.asarray(tops)))
        tau = sweep_tau(conf, np.asarray(margins), np.asarray(correct), delta, target_precision)
        if tau is None:
            notes.append(f"no tau in [0.50, 0.99] reaches precision {target_precision}")
        else:
            correct_tops = np.asarray(tops)[np.asarray(correct)]
            meta["rho"] = round(float(np.percentile(correct_tops, 1)), 6) if correct_tops.size else 0.0
            accepted = (conf >= tau) & (np.asarray(margins) >= delta) & (np.asarray(tops) >= meta["rho"])
            meta["tau"] = tau
            meta["enabled"] = True
            coverage = float(accepted.mean())
            precision = float(np.asarray(correct)[accepted].mean())

    meta["index_version"] = _version(meta, vec_arr, lab_arr, cen_arr, calibration)
    index = ClassIndex(meta, vec_arr, lab_arr, cen_arr, calibration)
    report = BuildReport(skipped, index.enabled, meta["tau"], delta, len(validation), precision, coverage, notes)
    return index, report
