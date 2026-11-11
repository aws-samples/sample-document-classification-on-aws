# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from service.common.aws import valid_namespace
from service.common.http import HttpError

SCOPES = ("classify:write", "jobs:read", "labels:write")


@dataclass(frozen=True)
class Identity:
    namespace: str
    principal: str
    scopes: frozenset[str]
    consumer: dict


def _role_arn(user_arn: str) -> str:
    parts = user_arn.split(":")
    if len(parts) >= 6 and parts[2] == "sts" and parts[5].startswith("assumed-role/"):
        role = parts[5].split("/")[1]
        return f"arn:aws:iam::{parts[4]}:role/{role}"
    return user_arn


def resolve(event: dict, consumers_table: Any, required_scope: str | None = None) -> Identity:
    ctx = event.get("requestContext") or {}
    claims = (ctx.get("authorizer") or {}).get("claims") or {}
    client_id = claims.get("client_id")
    if client_id:
        item = consumers_table.get_item(Key={"pk": f"client#{client_id}"}).get("Item")
        if not item or not valid_namespace(item.get("namespace", "")):
            raise HttpError(403, "unknown_consumer")
        api_key_id = (ctx.get("identity") or {}).get("apiKeyId")
        if not api_key_id or api_key_id != item.get("api_key_id"):
            raise HttpError(403, "api_key_mismatch")
        raw_scopes = claims.get("scope", "")
        scopes = frozenset(s.split("/", 1)[-1] for s in raw_scopes.split() if s)
        if required_scope and required_scope not in scopes:
            raise HttpError(403, "insufficient_scope")
        return Identity(item["namespace"], f"client#{client_id}", scopes, dict(item))
    user_arn = (ctx.get("identity") or {}).get("userArn")
    if user_arn:
        principal = _role_arn(user_arn)
        item = consumers_table.get_item(Key={"pk": f"iam#{principal}"}).get("Item")
        if not item or not valid_namespace(item.get("namespace", "")):
            raise HttpError(403, "unknown_consumer")
        return Identity(item["namespace"], f"iam#{principal}", frozenset(SCOPES), dict(item))
    raise HttpError(401, "unauthenticated")


def consumer_for_namespace(consumers_table: Any, namespace: str) -> dict:
    return consumers_table.get_item(Key={"pk": f"ns#{namespace}#consumer"}).get("Item") or {}
