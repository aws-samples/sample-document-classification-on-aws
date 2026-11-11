# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import re
import threading
import time
from typing import Any

import boto3
from botocore.config import Config

_NS = re.compile(r"^[a-z][a-z0-9-]{1,30}$")


def valid_namespace(namespace: str) -> bool:
    return bool(_NS.match(namespace or ""))


def config(timeout_s: float = 5.0, attempts: int = 3) -> Config:
    return Config(connect_timeout=timeout_s, read_timeout=timeout_s,
                  retries={"mode": "standard", "total_max_attempts": attempts})


class NamespaceSessions:
    def __init__(self, role_arn: str | None, region: str, sts: Any = None, refresh_margin_s: int = 300):
        self.role_arn = role_arn
        self.region = region
        self._sts = sts
        self._margin = refresh_margin_s
        self._cache: dict[str, tuple[boto3.Session, float]] = {}
        self._lock = threading.Lock()

    def _sts_client(self) -> Any:
        if self._sts is None:
            self._sts = boto3.client("sts", region_name=self.region, config=config())
        return self._sts

    def session(self, namespace: str) -> boto3.Session:
        if not valid_namespace(namespace):
            raise ValueError("invalid namespace")
        if not self.role_arn:
            return boto3.Session(region_name=self.region)
        with self._lock:
            cached = self._cache.get(namespace)
            if cached and cached[1] - self._margin > time.time():
                return cached[0]
            resp = self._sts_client().assume_role(
                RoleArn=self.role_arn,
                RoleSessionName=f"ns-{namespace}"[:64],
                Tags=[{"Key": "namespace", "Value": namespace}],
                DurationSeconds=3600,
            )
            c = resp["Credentials"]
            s = boto3.Session(aws_access_key_id=c["AccessKeyId"], aws_secret_access_key=c["SecretAccessKey"],
                              aws_session_token=c["SessionToken"], region_name=self.region)
            exp = c["Expiration"]
            self._cache[namespace] = (s, exp.timestamp() if hasattr(exp, "timestamp") else time.time() + 3600)
            return s
