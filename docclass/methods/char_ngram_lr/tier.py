# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from typing import Callable

import numpy as np

from docclass.core.config import TierRegistry, TierSettings
from docclass.core.errors import ConfigError
from docclass.core.types import ClassDecision, DecisionFactory, DocumentText
from docclass.methods.char_ngram_lr.preprocess import prepare_text
from docclass.methods.char_ngram_lr.scorer import ArtefactSource, Scorer

TIER_NAME = "char_ngram_lr"


class CharNgramTier:
    def __init__(self, scorer: Scorer, settings: TierSettings, decisions: DecisionFactory,
                 max_pages: int | None = None, min_chars: int | None = None):
        if scorer.taxonomy_version != decisions.taxonomy_version:
            raise ConfigError(f"artefact taxonomy {scorer.taxonomy_version} differs from cascade taxonomy "
                              f"{decisions.taxonomy_version}")
        outside = sorted(set(scorer.classes) - decisions.taxonomy)
        if outside:
            raise ConfigError(f"artefact classes outside the taxonomy: {outside}")
        self.name = settings.name
        self.scorer = scorer
        self.settings = settings
        self.decisions = decisions
        opts = settings.options
        self.max_pages = int(max_pages if max_pages is not None else opts.get("max_pages", scorer.max_pages))
        self.min_chars = int(min_chars if min_chars is not None else opts.get("min_chars", 50))
        self.defaults = scorer.thresholds

    def _threshold(self, key: str, class_id: str) -> float:
        return self.settings.threshold(key, class_id, default=self.defaults[key])

    def _abstain(self, reason: str, scores: dict[str, float] | None = None, class_id: str | None = None,
                 confidence: float = 0.0) -> ClassDecision:
        kw = {"class_id": class_id} if class_id else {}
        return self.decisions.abstain(reason, method=TIER_NAME, model_version=self.scorer.model_version,
                                      confidence=confidence, scores=scores, **kw)

    def classify(self, doc: DocumentText) -> ClassDecision:
        if not self.scorer.enabled:
            return self._abstain("tier disabled")
        text = prepare_text(doc, self.max_pages)
        if len(text) < self.min_chars:
            return self._abstain("insufficient text")
        x = self.scorer.vectorize(text)
        p = self.scorer.proba_from_vector(x)
        order = np.argsort(-p, kind="stable")
        classes = self.scorer.classes
        top, top_p = classes[order[0]], float(p[order[0]])
        second, second_p = (classes[order[1]], float(p[order[1]])) if len(order) > 1 else ("", 0.0)
        scores = {classes[i]: float(p[i]) for i in order[:5]}
        top_p = min(max(top_p, 0.0), 1.0)
        if top_p < self._threshold("tau", top):
            return self._abstain("low confidence", scores, top, top_p)
        if top_p - second_p < self._threshold("delta", top):
            return self._abstain(f"ambiguous: {top} vs {second}", scores, top, top_p)
        sim = self.scorer.centroid_similarity(int(order[0]), x)
        if sim < self._threshold("rho", top):
            return self._abstain("out of distribution", scores, top, top_p)
        return self.decisions.accept(top, top_p, method=TIER_NAME, model_version=self.scorer.model_version,
                                     scores=scores, metadata={"centroid_similarity": round(sim, 6)})


def register(registry: TierRegistry, load_artefact: Callable[[], ArtefactSource]) -> TierRegistry:
    def factory(settings: TierSettings, decisions: DecisionFactory) -> CharNgramTier:
        return CharNgramTier(Scorer.load(load_artefact()), settings, decisions)

    return registry.register(TIER_NAME, factory)
