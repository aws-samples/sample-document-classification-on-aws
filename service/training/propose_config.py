# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
from typing import Any

from docclass.core import load_config
from docclass.core.config import REMOVED_TIERS
from service.common.logs import log
from service.common.schema import validate
from service.common.settings import settings

TIER_KEYS = {"ngram": ("char_ngram_lr", "artefact_version"), "titan": ("titan_nn", "index_version")}


def _latest(appconfig: Any, app: str, profile: str) -> tuple[dict, int]:
    versions = appconfig.list_hosted_configuration_versions(ApplicationId=app, ConfigurationProfileId=profile,
                                                            MaxResults=1)["Items"]
    if not versions:
        raise LookupError("no hosted configuration")
    number = int(versions[0]["VersionNumber"])
    content = appconfig.get_hosted_configuration_version(ApplicationId=app, ConfigurationProfileId=profile,
                                                         VersionNumber=number)["Content"]
    raw = content.read() if hasattr(content, "read") else content
    return json.loads(raw), number


def propose(current: dict, meta: dict) -> dict:
    tier_name, option = TIER_KEYS[meta["tier"]]
    cfg = json.loads(json.dumps(current))
    cfg["tiers"] = [t for t in cfg.get("tiers", []) if t.get("name") not in REMOVED_TIERS]
    tiers = cfg["tiers"]
    entry = next((t for t in tiers if t.get("name") == tier_name), None)
    if entry is None:
        entry = {"name": tier_name}
        tiers.insert(0, entry)
    entry[option] = meta["model_version"]
    thresholds = {k.removeprefix("threshold_"): float(v) for k, v in meta.items() if k.startswith("threshold_")}
    if thresholds:
        entry["thresholds"] = {**entry.get("thresholds", {}), **thresholds}
    cfg["config_version"] = f"{current.get('config_version', 'cfg')}+{meta['tier']}-{meta['model_version']}"[:120]
    problems = validate(cfg)
    if problems:
        raise ValueError(f"proposed configuration is invalid: {problems[:3]}")
    load_config(cfg)
    return cfg


def handler(event: dict, context: Any = None, *, sm: Any = None, appconfig: Any = None,
            consumers: Any = None) -> dict:
    s = settings()
    detail = event.get("detail") or {}
    if detail.get("ModelApprovalStatus") != "Approved":
        return {"proposed": False, "reason": "not approved"}
    if sm is None or appconfig is None or consumers is None:
        import boto3
        from service.common.aws import config
        sm = sm or boto3.client("sagemaker", region_name=s.region, config=config(10, 3))
        appconfig = appconfig or boto3.client("appconfig", region_name=s.region, config=config(10, 3))
        consumers = consumers or boto3.resource("dynamodb", region_name=s.region, config=config(2, 3)).Table(
            s.consumers_table)
    pkg = sm.describe_model_package(ModelPackageName=detail["ModelPackageArn"])
    meta = pkg.get("CustomerMetadataProperties") or {}
    if meta.get("tier") not in TIER_KEYS or not meta.get("namespace"):
        return {"proposed": False, "reason": "not a docclass package"}
    ns = meta["namespace"]
    rec = consumers.get_item(Key={"pk": f"ns#{ns}#consumer"}).get("Item") or {}
    profile = rec.get("appconfig_profile_id")
    if not profile:
        raise LookupError(f"namespace {ns} has no configuration profile")
    current, number = _latest(appconfig, s.appconfig_application, profile)
    cfg = propose(current, meta)
    created = appconfig.create_hosted_configuration_version(
        ApplicationId=s.appconfig_application, ConfigurationProfileId=profile,
        Content=json.dumps(cfg).encode("utf-8"), ContentType="application/json",
        Description=f"proposed from {meta['tier']} {meta['model_version']}"[:1024],
        LatestVersionNumber=number)
    log("config_proposed", namespace=ns, tier=meta["tier"], model_version=meta["model_version"],
        config_version=cfg["config_version"])
    return {"proposed": True, "namespace": ns, "version_number": created["VersionNumber"],
            "config_version": cfg["config_version"]}
