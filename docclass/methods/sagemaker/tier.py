# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable

from docclass.core import CascadeContext, ClassDecision, ConfigError, DecisionFactory, DocumentText, TierRegistry, TierSettings
from docclass.methods.sagemaker.client import EndpointClient, EndpointFailure, EndpointThrottled, EndpointTimeout
from docclass.methods.sagemaker.contract import OTHER, InvalidResponse, VersionMismatch, build_request, parse_response
from docclass.text import prepare

log = logging.getLogger("docclass.sagemaker")

METHOD = "sagemaker"


class SageMakerTier:
    def __init__(self, settings: TierSettings, decisions: DecisionFactory, client: EndpointClient,
                 sleep: Callable[[float], None] = time.sleep, rng: Callable[[], float] = random.random):
        self.name = settings.name
        self.settings = settings
        self.decisions = decisions
        self.client = client
        self.sleep = sleep
        self.rng = rng
        opts = settings.options
        enabled = opts.get("enabled_for")
        if not isinstance(enabled, (list, tuple)) or not enabled:
            raise ConfigError(f"tier {self.name}: enabled_for must be a non-empty list")
        unknown = [c for c in enabled if c not in decisions.taxonomy]
        if unknown:
            raise ConfigError(f"tier {self.name}: enabled_for names unknown classes {unknown}")
        self.enabled_for = frozenset(enabled)
        self.gate_top_k = int(opts.get("gate_top_k", 3))
        self.model_version = opts.get("model_version") or None
        self.max_retries = int(opts.get("max_retries", 2))
        self.backoff_base_ms = float(opts.get("backoff_base_ms", 200))
        self.max_payload_bytes = int(opts.get("max_payload_bytes", 4_000_000))
        self.max_pages = int(opts.get("max_pages", 2))

    def _abstain(self, reason: str, *, model_version: str | None = None, scores: dict | None = None,
                 metadata: dict | None = None) -> ClassDecision:
        return self.decisions.abstain(reason, method=METHOD, model_version=model_version or self.model_version or "n/a",
                                      scores={k: v for k, v in (scores or {}).items() if k != OTHER},
                                      metadata=metadata)

    def _invoke(self, body: bytes) -> bytes:
        attempt = 0
        while True:
            try:
                return self.client.invoke(body)
            except EndpointThrottled:
                if attempt >= self.max_retries:
                    raise
                self.sleep(self.rng() * self.backoff_base_ms * (2 ** attempt) / 1000.0)
                attempt += 1

    def classify(self, doc: DocumentText, ctx: CascadeContext | None = None) -> ClassDecision:
        earlier = ctx.top_classes(self.gate_top_k) if ctx is not None else set()
        if not earlier & self.enabled_for:
            return self._abstain("not gated")
        pages = [p.number for p in doc.pages[:self.max_pages]]
        request = build_request(prepare(doc, self.max_pages), pages, self.model_version, self.max_payload_bytes)
        meta: dict[str, Any] = {"truncated": request.truncated}
        try:
            raw = self._invoke(request.body)
        except EndpointThrottled:
            return self._abstain("endpoint throttled", metadata=meta)
        except EndpointTimeout:
            return self._abstain("endpoint timeout", metadata=meta)
        except EndpointFailure as e:
            if e.code.endswith("ValidationError") or e.code == "ValidationException":
                log.error("sagemaker endpoint rejected the request: %s", e.code)
            return self._abstain(f"endpoint error: {e.code}", metadata=meta)
        try:
            response = parse_response(raw, self.decisions.taxonomy, self.model_version)
        except VersionMismatch as e:
            return self._abstain("model version mismatch", model_version=str(e), metadata=meta)
        except InvalidResponse:
            return self._abstain("invalid response", metadata=meta)
        ranked = sorted(response.probabilities.items(), key=lambda kv: kv[1], reverse=True)
        top, p_top = ranked[0]
        second, p_second = ranked[1] if len(ranked) > 1 else ("", 0.0)
        scores = dict(ranked[:5])
        version = response.model_version
        if top == OTHER:
            return self._abstain("other", model_version=version, scores=scores, metadata=meta)
        if top not in self.enabled_for:
            return self._abstain("outside gated set", model_version=version, scores=scores, metadata=meta)
        if p_top < self.settings.threshold("tau", top, default=0.9):
            return self._abstain("low confidence", model_version=version, scores=scores, metadata=meta)
        if p_top - p_second < self.settings.threshold("delta", top, default=0.1):
            return self._abstain(f"ambiguous: {top} vs {second}", model_version=version, scores=scores, metadata=meta)
        return self.decisions.accept(top, p_top, method=METHOD, model_version=version,
                                     scores={k: v for k, v in scores.items() if k != OTHER}, metadata=meta)


def register(registry: TierRegistry, client: EndpointClient | Callable[[TierSettings], EndpointClient],
             **kwargs: Any) -> TierRegistry:
    def factory(settings: TierSettings, decisions: DecisionFactory) -> SageMakerTier:
        c = client(settings) if callable(client) and not hasattr(client, "invoke") else client
        return SageMakerTier(settings, decisions, c, **kwargs)
    return registry.register(METHOD, factory)
