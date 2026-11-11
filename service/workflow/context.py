# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from service.common.aws import NamespaceSessions, config, valid_namespace
from service.common.jobs import JobStore
from service.common.settings import settings


@dataclass
class WorkflowContext:
    namespace: str
    s3: Any
    jobs: JobStore
    bucket: str


@lru_cache(maxsize=1)
def _sessions() -> NamespaceSessions:
    s = settings()
    return NamespaceSessions(s.data_access_role_arn, s.region)


def workflow_context(namespace: str, *, s3: Any = None, jobs: Any = None, bucket: str | None = None) -> WorkflowContext:
    if not valid_namespace(namespace):
        raise ValueError("invalid namespace")
    s = settings()
    if s3 is None or jobs is None:
        session = _sessions().session(namespace)
        s3 = s3 or session.client("s3", config=config(10, 3))
        jobs = jobs or JobStore(session.resource("dynamodb", config=config(2, 3)).Table(s.jobs_table))
    return WorkflowContext(namespace, s3, jobs, bucket or s.data_bucket)
