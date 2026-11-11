# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import inspect
import logging
import threading
import time
from typing import Any, Callable, Sequence

from docclass.core.config import CascadeConfig, TierRegistry
from docclass.core.errors import TierTimeout
from docclass.core.protocol import Classifier, PostHook
from docclass.core.types import (
    UNKNOWN,
    CascadeContext,
    CascadeResult,
    ClassDecision,
    DecisionFactory,
    DocumentText,
    TierAttempt,
)

log = logging.getLogger("docclass.cascade")


def run_with_timeout(fn: Callable[..., Any], args: tuple, timeout_s: float) -> Any:
    box: dict[str, Any] = {}
    done = threading.Event()

    def target() -> None:
        try:
            box["value"] = fn(*args)
        except BaseException as e:
            box["error"] = e
        finally:
            done.set()

    threading.Thread(target=target, daemon=True, name="docclass-tier").start()
    if not done.wait(timeout_s):
        raise TierTimeout()
    if "error" in box:
        raise box["error"]
    return box["value"]


def _wants_context(tier: Classifier) -> bool:
    try:
        params = [p for p in inspect.signature(tier.classify).parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    except (TypeError, ValueError):
        return False
    return len(params) >= 2


class Cascade:
    def __init__(self, tiers: Sequence[Classifier], config: CascadeConfig,
                 post_hooks: Sequence[PostHook] = (), timeouts_ms: dict[str, int | None] | None = None):
        self.tiers = tuple(tiers)
        self.config = config
        self.decisions: DecisionFactory = config.decision_factory()
        self.post_hooks = tuple(post_hooks)
        if timeouts_ms is None:
            timeouts_ms = {t.name: t.timeout_ms for t in config.tiers}
        self._timeouts = timeouts_ms
        self._context = {t.name: _wants_context(t) for t in self.tiers}

    @classmethod
    def from_config(cls, config: CascadeConfig, registry: TierRegistry,
                    post_hooks: Sequence[PostHook] = ()) -> Cascade:
        decisions = config.decision_factory()
        tiers = [registry.build(s, decisions) for s in config.tiers]
        return cls(tiers, config, post_hooks)

    def _call(self, tier: Classifier, doc: DocumentText, attempts: list[TierAttempt]) -> ClassDecision:
        args: tuple = (doc, CascadeContext(tuple(attempts))) if self._context[tier.name] else (doc,)
        timeout = self._timeouts.get(tier.name)
        if timeout is None:
            return tier.classify(*args)
        return run_with_timeout(tier.classify, args, timeout / 1000.0)

    def _attempt(self, tier: Classifier, doc: DocumentText, attempts: list[TierAttempt]) -> TierAttempt:
        start = time.perf_counter()
        try:
            d = self.decisions.check(self._call(tier, doc, attempts))
            return TierAttempt(tier.name, d, None, (time.perf_counter() - start) * 1000)
        except TierTimeout:
            d = self.decisions.abstain("timeout", method=tier.name)
            return TierAttempt(tier.name, d, "timeout", (time.perf_counter() - start) * 1000)
        except Exception as e:
            name = type(e).__name__
            log.warning("tier %s raised %s", tier.name, name)
            d = self.decisions.abstain(f"error: {name}", method=tier.name)
            return TierAttempt(tier.name, d, name, (time.perf_counter() - start) * 1000)

    def _unknown(self, reason: str) -> ClassDecision:
        return self.decisions.abstain(reason, method="cascade", model_version=self.config.config_version)

    def _finish(self, decision: ClassDecision, attempts: list[TierAttempt], doc: DocumentText) -> CascadeResult:
        result = CascadeResult(decision, tuple(attempts), doc.doc_hash, self.config.taxonomy_version,
                               self.config.config_version)
        for hook in self.post_hooks:
            try:
                hook.after(result, doc)
            except Exception as e:
                log.warning("post hook %s failed: %s", type(hook).__name__, type(e).__name__)
        return result

    def classify(self, doc: DocumentText) -> CascadeResult:
        attempts: list[TierAttempt] = []
        pending: ClassDecision | None = None
        pending_tier: str | None = None
        for tier in self.tiers:
            attempt = self._attempt(tier, doc, attempts)
            attempts.append(attempt)
            d = attempt.decision
            if not d.accepted:
                continue
            if pending is not None:
                if d.class_id == pending.class_id and tier.name != pending_tier:
                    return self._finish(pending, attempts, doc)
                return self._finish(self._unknown("tier disagreement"), attempts, doc)
            if not self.config.requires_agreement(d.class_id):
                return self._finish(d, attempts, doc)
            pending, pending_tier = d, tier.name
        if pending is not None:
            return self._finish(self._unknown(f"agreement not reached: {pending.class_id}"), attempts, doc)
        reason = "; ".join(f"{a.tier}: {a.decision.reason}" for a in attempts) or "no tiers configured"
        return self._finish(self._unknown(reason), attempts, doc)
