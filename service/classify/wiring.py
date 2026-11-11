# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from docclass.core.config import TierRegistry, TierSettings
from docclass.core.types import ClassDecision, DecisionFactory, DocumentText

TIER_NAMES = ("char_ngram_lr", "titan_nn", "sagemaker")


@dataclass
class Deps:
    embedder: Any = None
    emb_cache: Any = None
    load_ngram: Callable[[], Any] | None = None
    load_titan_index: Callable[[TierSettings], Any] | None = None
    sagemaker_client: Callable[[TierSettings], Any] | None = None


class Unprovisioned:
    def __init__(self, name: str, decisions: DecisionFactory, what: str):
        self.name = name
        self.decisions = decisions
        self.what = what

    def classify(self, doc: DocumentText) -> ClassDecision:
        return self.decisions.abstain(f"not provisioned: {self.what}", method=self.name)


def _placeholder(registry: TierRegistry, name: str, what: str) -> None:
    registry.register(name, lambda s, f: Unprovisioned(s.name, f, what))


def build_registry(namespace: str, deps: Deps) -> TierRegistry:
    registry = TierRegistry()
    if deps.load_ngram is not None:
        from docclass.methods.char_ngram_lr.tier import register as reg_ngram
        reg_ngram(registry, deps.load_ngram)
    else:
        _placeholder(registry, "char_ngram_lr", "artefact")
    if deps.embedder is not None and deps.load_titan_index is not None:
        from docclass.methods.titan_nn.tier import register as reg_titan
        reg_titan(registry, deps.embedder, deps.emb_cache, deps.load_titan_index, namespace)
    else:
        _placeholder(registry, "titan_nn", "index")
    if deps.sagemaker_client is not None:
        from docclass.methods.sagemaker.tier import register as reg_smkr
        reg_smkr(registry, deps.sagemaker_client)
    else:
        _placeholder(registry, "sagemaker", "endpoint")
    return registry


def tier_option(raw_config: dict, tier: str, key: str) -> Any:
    for t in raw_config.get("tiers", []):
        if t.get("name") == tier:
            return t.get(key)
    return None


def aws_deps(namespace: str, raw_config: dict, session: Any, *, settings: Any, artefacts: Any,
             function_session: Any) -> Deps:
    from service.common.aws import config as aws_config
    from service.common.storage import artefact_key

    s3 = session.client("s3", config=aws_config(5, 3))
    deps = Deps()

    ngram_version = tier_option(raw_config, "char_ngram_lr", "artefact_version")
    if ngram_version:
        ngram_key = artefact_key(namespace, "ngram", str(ngram_version))
        deps.load_ngram = lambda: artefacts.load(s3, settings.artefact_bucket, ngram_key)

    index_version = tier_option(raw_config, "titan_nn", "index_version")
    if index_version:
        from docclass.methods.titan_nn.emb_cache import DynamoDBEmbeddingCache
        from docclass.methods.titan_nn.embedder import BedrockTitanEmbedder
        from docclass.methods.titan_nn.index import ClassIndex
        titan_key = artefact_key(namespace, "titan", str(index_version))
        dims = int(tier_option(raw_config, "titan_nn", "dimensions") or 512)
        timeout = int(tier_option(raw_config, "titan_nn", "embed_timeout_ms") or 2000)
        deps.embedder = BedrockTitanEmbedder(
            function_session.client("bedrock-runtime", config=aws_config(timeout / 1000, 3)),
            dimensions=dims, timeout_ms=timeout)
        deps.emb_cache = DynamoDBEmbeddingCache(settings.decisions_table, session.client("dynamodb", config=aws_config(0.5, 2)))
        deps.load_titan_index = lambda s: ClassIndex.load(artefacts.load(s3, settings.artefact_bucket, titan_key))

    endpoint = tier_option(raw_config, "sagemaker", "endpoint_name")
    if endpoint:
        from docclass.methods.sagemaker.client import SageMakerRuntimeClient

        def client(s: TierSettings) -> Any:
            t = int(s.options.get("endpoint_timeout_ms", 8000))
            return SageMakerRuntimeClient(str(endpoint), endpoint_timeout_ms=t,
                                          boto_client=function_session.client(
                                              "sagemaker-runtime",
                                              config=aws_config(t / 1000, 1)))
        deps.sagemaker_client = client
    return deps
