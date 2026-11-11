# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping

from docclass.core.errors import ConfigError
from docclass.core.protocol import Classifier
from docclass.core.types import DecisionFactory

_RESERVED = {"name", "timeout_ms", "thresholds", "per_class", "enabled"}
# Tiers that no longer exist. Configurations written before their removal still
# name them, so they are skipped with a warning rather than rejected.
REMOVED_TIERS = frozenset({"duplicate_cache"})

log = logging.getLogger("docclass.config")


@dataclass(frozen=True)
class TierSettings:
    name: str
    timeout_ms: int | None
    thresholds: Mapping[str, float]
    per_class: Mapping[str, Mapping[str, float]]
    options: Mapping[str, Any]
    classes: frozenset[str] = frozenset()

    def threshold(self, key: str, class_id: str | None = None, default: float | None = None) -> float:
        if class_id is not None and key in self.per_class.get(class_id, {}):
            return float(self.per_class[class_id][key])
        if key in self.thresholds:
            return float(self.thresholds[key])
        if default is not None:
            return float(default)
        raise ConfigError(f"tier {self.name}: threshold {key!r} is not configured")


@dataclass(frozen=True)
class CascadeConfig:
    config_version: str
    taxonomy_version: str
    classes: frozenset[str]
    tiers: tuple[TierSettings, ...]
    agreement: frozenset[str] = frozenset()
    raw: Mapping[str, Any] = field(default_factory=dict)

    def requires_agreement(self, class_id: str) -> bool:
        return class_id in self.agreement

    def decision_factory(self) -> DecisionFactory:
        return DecisionFactory(self.classes, self.taxonomy_version)


TierFactory = Callable[[TierSettings, DecisionFactory], Classifier]


class TierRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, TierFactory] = {}

    def register(self, name: str, factory: TierFactory) -> TierRegistry:
        self._factories[name] = factory
        return self

    def __contains__(self, name: str) -> bool:
        return name in self._factories

    def build(self, settings: TierSettings, decisions: DecisionFactory) -> Classifier:
        if settings.name not in self._factories:
            raise ConfigError(f"unknown tier: {settings.name}")
        tier = self._factories[settings.name](settings, decisions)
        if not hasattr(tier, "classify") or not hasattr(tier, "name"):
            raise ConfigError(f"factory for {settings.name} did not return a classifier")
        return tier


def _unit(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}: threshold must be a number, got {value!r}")
    if not 0.0 <= float(value) <= 1.0:
        raise ConfigError(f"{where}: threshold {value} is outside [0, 1]")
    return float(value)


def _require(doc: Mapping[str, Any], key: str, kind: type) -> Any:
    if key not in doc:
        raise ConfigError(f"missing required key: {key}")
    if not isinstance(doc[key], kind):
        raise ConfigError(f"{key} must be {kind.__name__}")
    return doc[key]


def load_config(source: str | bytes | Mapping[str, Any], registry: TierRegistry | None = None) -> CascadeConfig:
    if isinstance(source, (str, bytes)):
        try:
            doc = json.loads(source)
        except json.JSONDecodeError as e:
            raise ConfigError(f"configuration is not valid JSON: {e}") from e
    else:
        doc = source
    if not isinstance(doc, Mapping):
        raise ConfigError("configuration must be a JSON object")

    config_version = str(_require(doc, "config_version", str))
    taxonomy_version = str(_require(doc, "taxonomy_version", str))
    classes_list = _require(doc, "classes", list)
    if not classes_list or not all(isinstance(c, str) and c for c in classes_list):
        raise ConfigError("classes must be a non-empty list of strings")
    if "UNKNOWN" in classes_list:
        raise ConfigError("UNKNOWN is reserved and cannot be a class")
    classes = frozenset(classes_list)
    if len(classes) != len(classes_list):
        raise ConfigError("classes contains duplicates")

    tiers_doc = _require(doc, "tiers", list)
    seen: set[str] = set()
    tiers: list[TierSettings] = []
    for i, t in enumerate(tiers_doc):
        if not isinstance(t, Mapping) or not isinstance(t.get("name"), str):
            raise ConfigError(f"tiers[{i}] must be an object with a name")
        name = t["name"]
        if name in REMOVED_TIERS:
            log.warning("ignoring removed tier %s in configuration %s", name, config_version)
            continue
        if name in seen:
            raise ConfigError(f"tier {name} is configured twice")
        seen.add(name)
        if registry is not None and name not in registry:
            raise ConfigError(f"unknown tier: {name}")
        if t.get("enabled", True) is False:
            continue
        timeout = t.get("timeout_ms")
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
            raise ConfigError(f"tier {name}: timeout_ms must be positive")
        thresholds = {k: _unit(v, f"tier {name}.{k}") for k, v in dict(t.get("thresholds") or {}).items()}
        per_class: dict[str, dict[str, float]] = {}
        for cls, values in dict(t.get("per_class") or {}).items():
            if cls not in classes:
                raise ConfigError(f"tier {name}: per_class names unknown class {cls}")
            per_class[cls] = {k: _unit(v, f"tier {name}.per_class.{cls}.{k}") for k, v in dict(values).items()}
        options = {k: v for k, v in t.items() if k not in _RESERVED}
        tiers.append(TierSettings(
            name=name,
            timeout_ms=int(timeout) if timeout is not None else None,
            thresholds=MappingProxyType(thresholds),
            per_class=MappingProxyType({k: MappingProxyType(v) for k, v in per_class.items()}),
            options=MappingProxyType(options),
            classes=classes,
        ))

    agreement_list = doc.get("agreement") or []
    if not isinstance(agreement_list, list):
        raise ConfigError("agreement must be a list")
    for cls in agreement_list:
        if cls not in classes:
            raise ConfigError(f"agreement names unknown class {cls}")

    return CascadeConfig(config_version, taxonomy_version, classes, tuple(tiers), frozenset(agreement_list),
                         MappingProxyType(dict(doc)))
