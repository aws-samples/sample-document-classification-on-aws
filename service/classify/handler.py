# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import base64
import binascii
import hashlib
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from docclass.core import Cascade, CascadeResult, ConfigError, DocumentText, PageText, load_config
from service.classify.wiring import Deps, aws_deps, build_registry
from service.common import emf
from service.common.http import HttpError, error_response, json_body, response
from service.common.logs import log


@dataclass
class Runtime:
    config_provider: Any
    deps_for: Callable[[str, dict], Deps]
    metric_namespace: str = "DocClass"
    stream: Any = None
    _cache: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def cascade(self, namespace: str) -> tuple[Cascade, dict]:
        raw = self.config_provider.get(namespace)
        key = (namespace, raw.get("config_version"))
        with self._lock:
            hit = self._cache.get(key)
        if hit:
            return hit, raw
        deps = self.deps_for(namespace, raw)
        registry = build_registry(namespace, deps)
        cfg = load_config(raw, registry)
        c = Cascade.from_config(cfg, registry)
        with self._lock:
            self._cache = {k: v for k, v in self._cache.items() if k[0] != namespace}
            self._cache[key] = c
        return c, raw


_runtime: Runtime | None = None


def default_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        import boto3
        from service.common.appconfig import ConfigProvider
        from service.common.aws import NamespaceSessions, config
        from service.common.settings import settings
        from service.common.storage import ArtefactCache
        s = settings()
        sessions = NamespaceSessions(s.data_access_role_arn, s.region)
        artefacts = ArtefactCache()
        function_session = boto3.Session(region_name=s.region)
        provider = ConfigProvider(function_session.client("appconfigdata", config=config(2, 3)), s.appconfig_application)
        _runtime = Runtime(
            config_provider=provider,
            deps_for=lambda ns, raw: aws_deps(ns, raw, sessions.session(ns), settings=s, artefacts=artefacts,
                                              function_session=function_session),
            metric_namespace=s.metric_namespace,
        )
    return _runtime


def set_runtime(runtime: Runtime | None) -> None:
    global _runtime
    _runtime = runtime


def handle(document_text: DocumentText, namespace: str, *, runtime: Runtime | None = None,
           ocr_pages: int = 0) -> CascadeResult:
    rt = runtime or default_runtime()
    cascade, raw = rt.cascade(namespace)
    result = cascade.classify(document_text)
    emf.emit(result, namespace, rt.metric_namespace, raw.get("unit_prices") or {}, ocr_pages, rt.stream)
    d = result.decision
    log("classified", stream=rt.stream, namespace=namespace, doc_hash=result.doc_hash, class_id=d.class_id,
        accepted=d.accepted, method=d.method, model_version=d.model_version, config_version=result.config_version)
    return result


def _document_from_body(body: dict, max_pages: int, min_chars: int, pdf_limit: int) -> DocumentText:
    if "pages" in body:
        pages = body["pages"]
        if not isinstance(pages, list) or not pages:
            raise HttpError(400, "pages_required")
        out = []
        for i, p in enumerate(pages):
            if not isinstance(p, dict) or not isinstance(p.get("text"), str):
                raise HttpError(400, "invalid_page", index=i)
            out.append(PageText(int(p.get("number", i + 1)), p["text"], "text_layer"))
        joined = "\f".join(p.text for p in out)
        return DocumentText(hashlib.sha256(joined.encode("utf-8")).hexdigest(), tuple(out))
    if "pdf_base64" in body:
        try:
            data = base64.b64decode(body["pdf_base64"], validate=True)
        except (binascii.Error, ValueError, TypeError):
            raise HttpError(400, "invalid_base64")
        if len(data) > pdf_limit:
            raise HttpError(413, "pdf_too_large", limit_bytes=pdf_limit, use="/v1/jobs")
        from service.text.pdf import PdfError, inspect_pdf
        try:
            info = inspect_pdf(data, max_pages=max_pages, min_chars=min_chars)
        except PdfError as e:
            raise HttpError(400, "unreadable_pdf", reason=str(e))
        if info.needs_ocr:
            raise HttpError(409, "ocr_required", use="/v1/jobs")
        return DocumentText.from_bytes(data, [t for _, t in info.pages], ["text_layer"] * len(info.pages))
    raise HttpError(400, "pages_or_pdf_required")


def api_handler(event: dict, context: Any = None, *, runtime: Runtime | None = None, consumers_table: Any = None,
                settings_obj: Any = None) -> dict:
    from service.common.identity import resolve
    try:
        if consumers_table is None or settings_obj is None:
            from service.api.deps import api_deps
            d = api_deps()
            consumers_table = consumers_table or d.consumers
            settings_obj = settings_obj or d.settings
        ident = resolve(event, consumers_table, "classify:write")
        doc = _document_from_body(json_body(event), settings_obj.max_pages, settings_obj.min_chars,
                                  settings_obj.sync_pdf_limit)
        try:
            result = handle(doc, ident.namespace, runtime=runtime)
        except (LookupError, ConfigError):
            raise HttpError(503, "namespace_not_configured")
        return response(200, result.to_dict())
    except HttpError as e:
        return error_response(e)


def workflow_handler(event: dict, context: Any = None, *, runtime: Runtime | None = None, s3: Any = None,
                     jobs: Any = None, bucket: str | None = None) -> dict:
    from service.common.jobs import event_detail
    from service.common.storage import text_key
    from service.workflow.context import workflow_context
    ns, job_id, doc_hash = event["namespace"], event["job_id"], event["doc_hash"]
    ctx = workflow_context(ns, s3=s3, jobs=jobs, bucket=bucket)
    ctx.jobs.set_status(ns, job_id, "Classifying")
    raw = ctx.s3.get_object(Bucket=ctx.bucket, Key=text_key(ns, doc_hash))["Body"].read()
    import json
    doc = DocumentText.from_dict(json.loads(raw))
    ocr_pages = sum(1 for p in doc.pages if p.source == "ocr")
    result = handle(doc, ns, runtime=runtime, ocr_pages=ocr_pages).to_dict()
    status = ctx.jobs.complete(ns, job_id, result, doc_hash=doc_hash, config_version=result["config_version"])
    return {"status": status, **event_detail(ns, job_id, result)}


def handler(event: dict, context: Any = None) -> dict:
    if "requestContext" in event or "httpMethod" in event:
        return api_handler(event, context)
    return workflow_handler(event, context)
