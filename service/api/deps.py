# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config

from service.common.aws import NamespaceSessions, config
from service.common.jobs import JobStore
from service.common.settings import Settings, settings


@dataclass
class ApiDeps:
    settings: Settings
    consumers: Any
    sessions: NamespaceSessions

    def jobs(self, namespace: str) -> JobStore:
        return JobStore(self.sessions.session(namespace).resource("dynamodb", config=config(2, 3))
                        .Table(self.settings.jobs_table))

    def s3(self, namespace: str) -> Any:
        # S3 client that presigns SigV4 URLs on the regional endpoint.
        return self.sessions.session(namespace).client("s3", config=config(5, 3).merge(
            Config(signature_version="s3v4", s3={"addressing_style": "virtual"})))


@lru_cache(maxsize=1)
def api_deps() -> ApiDeps:
    s = settings()
    consumers = boto3.resource("dynamodb", region_name=s.region, config=config(2, 3)).Table(s.consumers_table)
    return ApiDeps(s, consumers, NamespaceSessions(s.data_access_role_arn, s.region))
