# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import re
import time
from typing import Any

from service.common.aws import config
from service.common.http import HttpError, error_response, json_body, response
from service.common.identity import resolve
from service.common.logs import log
from service.common.storage import get_document_text, label_key, put_json, text_key

_HASH = re.compile(r"^[0-9a-f]{64}$")
_provider = None


def _config(namespace: str, provider: Any) -> dict:
    global _provider
    if provider is not None:
        return provider.get(namespace)
    if _provider is None:
        import boto3
        from service.common.appconfig import ConfigProvider
        from service.common.settings import settings
        s = settings()
        _provider = ConfigProvider(boto3.client("appconfigdata", region_name=s.region, config=config(2, 3)),
                                   s.appconfig_application)
    return _provider.get(namespace)


def handler(event: dict, context: Any = None, deps: Any = None, config_provider: Any = None) -> dict:
    try:
        if deps is None:
            from service.api.deps import api_deps
            deps = api_deps()
        ident = resolve(event, deps.consumers, "labels:write")
        body = json_body(event)
        doc_hash, class_id = body.get("doc_hash"), body.get("class_id")
        if not isinstance(doc_hash, str) or not _HASH.match(doc_hash) or not isinstance(class_id, str):
            raise HttpError(400, "doc_hash_and_class_id_required")
        ns = ident.namespace
        try:
            raw = _config(ns, config_provider)
        except LookupError:
            raise HttpError(503, "namespace_not_configured")
        if class_id not in raw["classes"]:
            raise HttpError(400, "unknown_class")
        s3 = deps.s3(ns)
        bucket = deps.settings.data_bucket
        doc = get_document_text(s3, bucket, ns, doc_hash)
        label = {"doc_hash": doc_hash, "class_id": class_id, "taxonomy_version": raw["taxonomy_version"],
                 "labelled_at": int(time.time()), "principal": ident.principal,
                 "text_key": text_key(ns, doc_hash) if doc is not None else None,
                 "document": doc.to_dict() if doc is not None else None}
        put_json(s3, bucket, label_key(ns, doc_hash), label)
        log("labelled", namespace=ns, doc_hash=doc_hash, class_id=class_id)
        return response(201, {"doc_hash": doc_hash, "class_id": class_id})
    except HttpError as e:
        return error_response(e)
