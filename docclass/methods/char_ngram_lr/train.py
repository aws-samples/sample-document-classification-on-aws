# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
from scipy import sparse
from sklearn.calibration import CalibratedClassifierCV
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression

from docclass.core.types import DocumentText
from docclass.methods.char_ngram_lr.preprocess import prepare_text


class TrainingError(Exception):
    pass


@dataclass
class TrainConfig:
    taxonomy: Sequence[str]
    taxonomy_version: str
    target_precision: float = 0.99
    max_pages: int = 2
    min_per_class: int = 10
    calibration_min: int = 30
    delta: float = 0.10
    rho_percentile: float = 1.0
    max_features: int = 20_000
    min_df: int = 2
    C: float = 10.0
    max_iter: int = 2000
    seed: int = 0
    val_fraction: float = 0.2
    calib_fraction: float = 0.2
    ngram_range: tuple[int, int] = (3, 5)
    model_version: str | None = None


@dataclass
class Split:
    texts: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    hashes: list[str] = field(default_factory=list)


@dataclass
class TrainedModel:
    config: TrainConfig
    vectorizer: TfidfVectorizer
    lr: LogisticRegression
    calibrator: CalibratedClassifierCV | None
    classes: list[str]
    centroids: sparse.csr_matrix
    thresholds: dict[str, float]
    enabled: bool
    report: dict[str, Any]
    train: Split
    calib: Split
    val: Split

    @property
    def model(self) -> Any:
        return self.calibrator if self.calibrator is not None else self.lr

    def predict_proba(self, texts: Sequence[str]) -> np.ndarray:
        return self.model.predict_proba(self.vectorizer.transform(list(texts)))


def split_by_document(dataset: Sequence[tuple[DocumentText, str]], max_pages: int, fractions: Sequence[float],
                      seed: int) -> list[Split]:
    by_hash: dict[str, list[tuple[str, str]]] = defaultdict(list)
    hash_class: dict[str, str] = {}
    for doc, label in dataset:
        by_hash[doc.doc_hash].append((prepare_text(doc, max_pages), label))
        hash_class.setdefault(doc.doc_hash, label)
    per_class: dict[str, list[str]] = defaultdict(list)
    for h in sorted(by_hash):
        per_class[hash_class[h]].append(h)
    rng = random.Random(seed)  # seeded shuffle for a reproducible data split  # nosec B311
    sides = [Split() for _ in fractions]
    for cls in sorted(per_class):
        hashes = per_class[cls]
        rng.shuffle(hashes)
        n = len(hashes)
        bounds, acc = [], 0.0
        for f in fractions[:-1]:
            acc += f
            bounds.append(int(round(acc * n)))
        bounds.append(n)
        start = 0
        for side, end in zip(sides, bounds):
            for h in hashes[start:end]:
                for text, label in by_hash[h]:
                    side.texts.append(text)
                    side.labels.append(label)
                    side.hashes.append(h)
            start = end
    return sides


def _centroids(X: sparse.csr_matrix, y: np.ndarray, n_classes: int) -> sparse.csr_matrix:
    rows = []
    for k in range(n_classes):
        m = np.asarray(X[y == k].mean(axis=0)).ravel()
        norm = np.linalg.norm(m)
        rows.append(m / norm if norm > 0 else m)
    c = sparse.csr_matrix(np.vstack(rows))
    c.eliminate_zeros()
    c.sort_indices()
    return c


def _row_sims(X: sparse.csr_matrix, centroids: sparse.csr_matrix, cls_idx: np.ndarray) -> np.ndarray:
    return np.asarray(X.multiply(centroids[cls_idx]).sum(axis=1)).ravel()


def _accept_mask(P: np.ndarray, sims: np.ndarray, tau: float, delta: float, rho: float) -> np.ndarray:
    srt = np.sort(P, axis=1)
    top = srt[:, -1]
    second = srt[:, -2] if P.shape[1] > 1 else np.zeros(len(P))
    return (top >= tau) & (top - second >= delta) & (sims >= rho)


def _sweep(P: np.ndarray, sims: np.ndarray, y: np.ndarray, delta: float, rho: float,
           target: float) -> tuple[float, bool, float]:
    pred = P.argmax(axis=1)
    best_tau, best_prec = 0.99, -1.0
    for tau in np.round(np.arange(0.50, 0.99 + 1e-9, 0.01), 2):
        acc = _accept_mask(P, sims, float(tau), delta, rho)
        if not acc.any():
            continue
        prec = float((pred[acc] == y[acc]).mean())
        if prec >= target:
            return float(tau), True, prec
        if prec > best_prec:
            best_tau, best_prec = float(tau), prec
    return best_tau, False, max(best_prec, 0.0)


def _report(P: np.ndarray, sims: np.ndarray, y: np.ndarray, classes: list[str], thresholds: dict[str, float],
            enabled: bool, excluded: dict[str, int], calibrated: bool, sizes: dict[str, int],
            target: float, achieved: float) -> dict[str, Any]:
    pred = P.argmax(axis=1)
    acc = _accept_mask(P, sims, thresholds["tau"], thresholds["delta"], thresholds["rho"])
    per_class = {}
    for k, cls in enumerate(classes):
        accepted_as = acc & (pred == k)
        correct = accepted_as & (y == k)
        support = int((y == k).sum())
        per_class[cls] = {
            "precision": float(correct.sum() / accepted_as.sum()) if accepted_as.any() else None,
            "recall": float(correct.sum() / support) if support else None,
            "support": support,
        }
    confusion = Counter((classes[t], classes[p]) for t, p in zip(y, pred) if t != p)
    report = {
        "classes": classes,
        "excluded": excluded,
        "calibrated": calibrated,
        "sizes": sizes,
        "thresholds": thresholds,
        "enabled": enabled,
        "target_precision": target,
        "achieved_precision": achieved,
        "per_class": per_class,
        "coverage": float(acc.mean()) if len(acc) else 0.0,
        "selective_accuracy": float((pred[acc] == y[acc]).mean()) if acc.any() else None,
        "confused_pairs": [{"true": t, "predicted": p, "count": n} for (t, p), n in confusion.most_common(5)],
    }
    if not enabled:
        report["message"] = (f"no tau in [0.50, 0.99] reaches target precision {target}; "
                             f"best was {achieved:.4f} at tau={thresholds['tau']}; artefact disabled")
    return report


def train(dataset: Sequence[tuple[DocumentText, str]], cfg: TrainConfig) -> TrainedModel:
    taxonomy = set(cfg.taxonomy)
    unknown = sorted({label for _, label in dataset} - taxonomy)
    if unknown:
        raise TrainingError(f"labels outside the taxonomy: {unknown}")
    doc_counts = Counter()
    seen: set[tuple[str, str]] = set()
    for doc, label in dataset:
        if (doc.doc_hash, label) not in seen:
            seen.add((doc.doc_hash, label))
            doc_counts[label] += 1
    excluded = {c: doc_counts.get(c, 0) for c in sorted(taxonomy) if doc_counts.get(c, 0) < cfg.min_per_class}
    classes = sorted(c for c in taxonomy if c not in excluded)
    if len(classes) < 2:
        raise TrainingError(f"fewer than two classes have at least {cfg.min_per_class} documents")
    kept = [(d, label) for d, label in dataset if label in classes]
    calibrate = all(doc_counts[c] >= cfg.calibration_min for c in classes)
    if calibrate:
        fractions = [1.0 - cfg.calib_fraction - cfg.val_fraction, cfg.calib_fraction, cfg.val_fraction]
        tr, ca, va = split_by_document(kept, cfg.max_pages, fractions, cfg.seed)
    else:
        tr, va = split_by_document(kept, cfg.max_pages, [1.0 - cfg.val_fraction, cfg.val_fraction], cfg.seed)
        ca = Split()

    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=tuple(cfg.ngram_range), lowercase=False,
                          sublinear_tf=True, min_df=cfg.min_df, max_features=cfg.max_features, dtype=np.float64)
    X_tr = vec.fit_transform(tr.texts)
    index = {c: i for i, c in enumerate(classes)}
    y_tr = np.array([index[label] for label in tr.labels])
    lr = LogisticRegression(C=cfg.C, max_iter=cfg.max_iter, class_weight="balanced")
    lr.fit(X_tr, y_tr)
    calibrator = None
    if calibrate:
        calibrator = CalibratedClassifierCV(FrozenEstimator(lr), method="sigmoid")
        calibrator.fit(vec.transform(ca.texts), np.array([index[label] for label in ca.labels]))
    centroids = _centroids(X_tr, y_tr, len(classes))
    train_sims = _row_sims(X_tr, centroids, y_tr)
    rho = float(np.clip(np.percentile(train_sims, cfg.rho_percentile), 0.0, 1.0))

    model = calibrator if calibrator is not None else lr
    if va.texts:
        X_va = vec.transform(va.texts)
        y_va = np.array([index[label] for label in va.labels])
        P_va = model.predict_proba(X_va)
        sims_va = _row_sims(X_va, centroids, P_va.argmax(axis=1))
    else:
        y_va, P_va, sims_va = np.empty(0, dtype=int), np.empty((0, len(classes))), np.empty(0)
    tau, enabled, achieved = _sweep(P_va, sims_va, y_va, cfg.delta, rho, cfg.target_precision)
    thresholds = {"tau": tau, "delta": float(cfg.delta), "rho": rho}
    sizes = {"train": len(tr.texts), "calibration": len(ca.texts), "validation": len(va.texts)}
    report = _report(P_va, sims_va, y_va, classes, thresholds, enabled, excluded, calibrate, sizes,
                     cfg.target_precision, achieved)
    return TrainedModel(cfg, vec, lr, calibrator, classes, centroids, thresholds, enabled, report, tr, ca, va)
