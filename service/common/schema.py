# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("config_schema.json")


def schema_text() -> str:
    return SCHEMA_PATH.read_text()


def schema() -> dict:
    return json.loads(schema_text())


def _type_ok(value, kind: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }[kind]


def validate(value, node: dict | None = None, path: str = "$") -> list[str]:
    node = schema() if node is None else node
    errors: list[str] = []
    kind = node.get("type")
    if kind and not _type_ok(value, kind):
        return [f"{path}: expected {kind}"]
    if "enum" in node and value not in node["enum"]:
        errors.append(f"{path}: not one of {node['enum']}")
    if "not" in node and not validate(value, node["not"], path):
        errors.append(f"{path}: forbidden value")
    if kind == "string":
        if len(value) < node.get("minLength", 0):
            errors.append(f"{path}: too short")
        if "pattern" in node:
            import re
            if not re.search(node["pattern"], value):
                errors.append(f"{path}: does not match {node['pattern']}")
    if kind == "number":
        if "minimum" in node:
            if value < node["minimum"] or (node.get("exclusiveMinimum") and value == node["minimum"]):
                errors.append(f"{path}: below minimum")
        if "maximum" in node and value > node["maximum"]:
            errors.append(f"{path}: above maximum")
    if kind == "array":
        if len(value) < node.get("minItems", 0):
            errors.append(f"{path}: too few items")
        if node.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            errors.append(f"{path}: duplicate items")
        for i, v in enumerate(value):
            errors += validate(v, node.get("items", {}), f"{path}[{i}]")
    if kind == "object":
        for r in node.get("required", []):
            if r not in value:
                errors.append(f"{path}: missing {r}")
        props = node.get("properties", {})
        extra = node.get("additionalProperties")
        for k, v in value.items():
            if k in props:
                errors += validate(v, props[k], f"{path}.{k}")
            elif isinstance(extra, dict):
                errors += validate(v, extra, f"{path}.{k}")
    return errors
