# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Mapping

from docclass.core.errors import InvalidDecisionError

UNKNOWN = "UNKNOWN"

TextSource = Literal["text_layer", "ocr"]


@dataclass(frozen=True)
class PageText:
    number: int
    text: str
    source: TextSource = "text_layer"

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError("page numbers are 1-based")
        if self.source not in ("text_layer", "ocr"):
            raise ValueError(f"unknown page source: {self.source}")


@dataclass(frozen=True)
class DocumentText:
    doc_hash: str
    pages: tuple[PageText, ...]

    @classmethod
    def from_texts(cls, texts: list[str], source: TextSource = "text_layer", doc_hash: str | None = None) -> DocumentText:
        pages = tuple(PageText(i + 1, t, source) for i, t in enumerate(texts))
        if doc_hash is None:
            doc_hash = hashlib.sha256("\f".join(texts).encode("utf-8")).hexdigest()
        return cls(doc_hash, pages)

    @classmethod
    def from_bytes(cls, data: bytes, texts: list[str], sources: list[TextSource] | None = None) -> DocumentText:
        sources = sources or ["text_layer"] * len(texts)
        pages = tuple(PageText(i + 1, t, s) for i, (t, s) in enumerate(zip(texts, sources)))
        return cls(hashlib.sha256(data).hexdigest(), pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_hash": self.doc_hash,
            "pages": [{"number": p.number, "text": p.text, "source": p.source} for p in self.pages],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DocumentText:
        return cls(
            str(data["doc_hash"]),
            tuple(PageText(int(p["number"]), str(p["text"]), p.get("source", "text_layer")) for p in data["pages"]),
        )


@dataclass(frozen=True)
class ClassDecision:
    class_id: str
    confidence: float
    accepted: bool
    method: str
    model_version: str
    taxonomy_version: str
    scores: Mapping[str, float] = field(default_factory=dict)
    reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        c = self.confidence
        if not isinstance(c, (int, float)) or isinstance(c, bool) or math.isnan(c) or c < 0.0 or c > 1.0:
            raise InvalidDecisionError(f"confidence must lie in [0, 1], got {c!r}")
        if self.accepted and self.class_id == UNKNOWN:
            raise InvalidDecisionError("an accepted decision cannot be UNKNOWN")
        if not self.accepted and not (self.reason and self.reason.strip()):
            raise InvalidDecisionError("an abstention must carry a reason")
        if not self.method:
            raise InvalidDecisionError("method is required")
        object.__setattr__(self, "confidence", float(c))
        object.__setattr__(self, "scores", MappingProxyType(dict(self.scores)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_id": self.class_id,
            "confidence": self.confidence,
            "accepted": self.accepted,
            "method": self.method,
            "model_version": self.model_version,
            "taxonomy_version": self.taxonomy_version,
            "scores": dict(self.scores),
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ClassDecision:
        return cls(
            class_id=str(data["class_id"]),
            confidence=float(data["confidence"]),
            accepted=bool(data["accepted"]),
            method=str(data["method"]),
            model_version=str(data["model_version"]),
            taxonomy_version=str(data["taxonomy_version"]),
            scores={k: float(v) for k, v in dict(data.get("scores") or {}).items()},
            reason=data.get("reason"),
            metadata=dict(data.get("metadata") or {}),
        )


class DecisionFactory:
    def __init__(self, taxonomy: frozenset[str] | set[str] | list[str], taxonomy_version: str):
        self.taxonomy = frozenset(taxonomy)
        self.taxonomy_version = taxonomy_version

    def accept(self, class_id: str, confidence: float, *, method: str, model_version: str,
               scores: Mapping[str, float] | None = None, metadata: Mapping[str, Any] | None = None) -> ClassDecision:
        if class_id not in self.taxonomy:
            raise InvalidDecisionError(f"class {class_id!r} is not in taxonomy {self.taxonomy_version}")
        return ClassDecision(class_id, confidence, True, method, model_version, self.taxonomy_version,
                             scores or {}, None, metadata or {})

    def abstain(self, reason: str, *, method: str, model_version: str = "n/a", class_id: str = UNKNOWN,
                confidence: float = 0.0, scores: Mapping[str, float] | None = None,
                metadata: Mapping[str, Any] | None = None) -> ClassDecision:
        return ClassDecision(class_id, confidence, False, method, model_version, self.taxonomy_version,
                             scores or {}, reason, metadata or {})

    def check(self, decision: ClassDecision) -> ClassDecision:
        if decision.accepted and decision.class_id not in self.taxonomy:
            raise InvalidDecisionError(f"class {decision.class_id!r} is not in taxonomy {self.taxonomy_version}")
        if decision.taxonomy_version != self.taxonomy_version:
            raise InvalidDecisionError(
                f"decision taxonomy {decision.taxonomy_version} differs from {self.taxonomy_version}")
        return decision


@dataclass(frozen=True)
class TierAttempt:
    tier: str
    decision: ClassDecision
    error: str | None
    elapsed_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {"tier": self.tier, "decision": self.decision.to_dict(), "error": self.error,
                "elapsed_ms": round(self.elapsed_ms, 3)}


@dataclass(frozen=True)
class CascadeContext:
    attempts: tuple[TierAttempt, ...]

    def top_classes(self, k: int = 3) -> set[str]:
        out: set[str] = set()
        for a in self.attempts:
            ranked = sorted(a.decision.scores.items(), key=lambda kv: kv[1], reverse=True)[:k]
            out.update(c for c, _ in ranked)
            if a.decision.class_id != UNKNOWN:
                out.add(a.decision.class_id)
        return out


@dataclass(frozen=True)
class CascadeResult:
    decision: ClassDecision
    attempts: tuple[TierAttempt, ...]
    doc_hash: str
    taxonomy_version: str
    config_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.to_dict(),
            "attempts": [a.to_dict() for a in self.attempts],
            "doc_hash": self.doc_hash,
            "taxonomy_version": self.taxonomy_version,
            "config_version": self.config_version,
        }
