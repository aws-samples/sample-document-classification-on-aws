# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import logging
from typing import Callable

import numpy as np

from docclass.core import ClassDecision, ConfigError, DecisionFactory, DocumentText, TierRegistry, TierSettings
from docclass.methods.titan_nn.emb_cache import EmbeddingCache, embedding_key
from docclass.methods.titan_nn.embedder import Embedder, EmbeddingUnavailable
from docclass.methods.titan_nn.index import ClassIndex, prepared_text

NAME = "titan_nn"
log = logging.getLogger("docclass.titan_nn")


class TitanNearestTier:
    def __init__(self, settings: TierSettings, decisions: DecisionFactory, embedder: Embedder,
                 emb_cache: EmbeddingCache | None, index: ClassIndex, namespace: str):
        self.name = settings.name
        self.settings = settings
        self.decisions = decisions
        self.embedder = embedder
        self.emb_cache = emb_cache
        self.index = index
        self.namespace = namespace
        meta = index.meta
        if meta["embedding_model_id"] != embedder.model_id or int(meta["dimensions"]) != embedder.dimensions:
            raise ConfigError(
                f"index {index.index_version} was built with {meta['embedding_model_id']}/{meta['dimensions']}, "
                f"embedder is {embedder.model_id}/{embedder.dimensions}")
        if meta["taxonomy_version"] != decisions.taxonomy_version:
            raise ConfigError(
                f"index taxonomy {meta['taxonomy_version']} differs from cascade {decisions.taxonomy_version}")
        outside = sorted(set(index.classes) - decisions.taxonomy)
        if outside:
            raise ConfigError(f"index names classes outside the taxonomy: {', '.join(outside)}")
        opts = settings.options
        self.k = int(opts.get("k", meta["k"]))
        self.max_pages = int(opts.get("max_pages", meta.get("max_pages", 2)))
        self.max_input_chars = int(opts.get("max_input_chars", meta.get("max_input_chars", 20_000)))
        self.min_chars = int(opts.get("min_chars", 50))

    def _abstain(self, reason: str, **kw) -> ClassDecision:
        return self.decisions.abstain(reason, method=NAME, model_version=self.index.index_version,
                                      metadata={"index_version": self.index.index_version}, **kw)

    def _embedding(self, text: str) -> np.ndarray:
        key = embedding_key(self.namespace, self.embedder.model_id, self.embedder.dimensions, text)
        if self.emb_cache is not None:
            try:
                hit = self.emb_cache.get(key)
                if hit is not None and hit.shape == (self.embedder.dimensions,):
                    return hit
            except Exception as e:
                log.warning("embedding cache read failed: %s", type(e).__name__)
        vec = self.embedder.embed(text)
        if self.emb_cache is not None:
            try:
                self.emb_cache.put(key, vec)
            except Exception as e:
                log.warning("embedding cache write failed: %s", type(e).__name__)
        return vec

    def classify(self, doc: DocumentText) -> ClassDecision:
        if not self.index.enabled:
            return self._abstain("index disabled")
        text = prepared_text(doc, self.max_pages, self.max_input_chars)
        if len(text) < self.min_chars:
            return self._abstain("insufficient text")
        try:
            q = self._embedding(text)
        except EmbeddingUnavailable as e:
            return self._abstain(f"embedding unavailable: {e.cause}")
        raw = self.index.raw_scores(q, self.k)
        classes = self.index.classes
        order = np.argsort(-raw)
        top = classes[order[0]]
        conf = float(self.index.calibration(float(raw[order[0]])))
        scores = {classes[i]: round(float(self.index.calibration(float(raw[i]))), 6) for i in order[:5]}
        tau = self.settings.threshold("tau", top, default=self.index.meta.get("tau") or 1.0)
        delta = self.settings.threshold("delta", top, default=self.index.meta.get("delta", 0.0))
        rho = self.settings.threshold("rho", top, default=self.index.meta.get("rho", 0.0))
        if float(raw[order[0]]) < rho:
            return self._abstain("out of distribution", scores=scores, confidence=conf)
        if conf < tau:
            return self._abstain("low confidence", scores=scores, confidence=conf)
        if len(order) > 1:
            second = classes[order[1]]
            if float(raw[order[0]] - raw[order[1]]) < delta:
                return self._abstain(f"ambiguous: {top} vs {second}", scores=scores, confidence=conf)
        return self.decisions.accept(top, conf, method=NAME, model_version=self.index.index_version, scores=scores,
                                     metadata={"index_version": self.index.index_version,
                                               "raw_score": round(float(raw[order[0]]), 6)})


def register(registry: TierRegistry, embedder: Embedder, emb_cache: EmbeddingCache | None,
             load_index: Callable[[TierSettings], ClassIndex], namespace: str) -> TierRegistry:
    return registry.register(NAME, lambda settings, decisions: TitanNearestTier(
        settings, decisions, embedder, emb_cache, load_index(settings), namespace))
