# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    region: str = field(default_factory=lambda: _env("AWS_REGION", _env("AWS_DEFAULT_REGION", "eu-west-1")))
    data_bucket: str = field(default_factory=lambda: _env("DATA_BUCKET"))
    artefact_bucket: str = field(default_factory=lambda: _env("ARTEFACT_BUCKET"))
    jobs_table: str = field(default_factory=lambda: _env("JOBS_TABLE"))
    decisions_table: str = field(default_factory=lambda: _env("DECISIONS_TABLE"))
    consumers_table: str = field(default_factory=lambda: _env("CONSUMERS_TABLE"))
    semaphore_table: str = field(default_factory=lambda: _env("SEMAPHORE_TABLE"))
    event_bus: str = field(default_factory=lambda: _env("EVENT_BUS"))
    data_access_role_arn: str = field(default_factory=lambda: _env("DATA_ACCESS_ROLE_ARN"))
    state_machine_arn: str = field(default_factory=lambda: _env("STATE_MACHINE_ARN"))
    textract_topic_arn: str = field(default_factory=lambda: _env("TEXTRACT_TOPIC_ARN"))
    textract_role_arn: str = field(default_factory=lambda: _env("TEXTRACT_ROLE_ARN"))
    appconfig_application: str = field(default_factory=lambda: _env("APPCONFIG_APPLICATION"))
    appconfig_deployment_strategy: str = field(default_factory=lambda: _env("APPCONFIG_DEPLOYMENT_STRATEGY"))
    appconfig_monitor_role_arn: str = field(default_factory=lambda: _env("APPCONFIG_MONITOR_ROLE_ARN"))
    user_pool_id: str = field(default_factory=lambda: _env("USER_POOL_ID"))
    resource_server: str = field(default_factory=lambda: _env("RESOURCE_SERVER", "docclass"))
    api_id: str = field(default_factory=lambda: _env("API_ID"))
    api_stage: str = field(default_factory=lambda: _env("API_STAGE", "v1"))
    alarm_topic_arn: str = field(default_factory=lambda: _env("ALARM_TOPIC_ARN"))
    metric_namespace: str = field(default_factory=lambda: _env("METRIC_NAMESPACE", "DocClass"))
    max_pages: int = field(default_factory=lambda: int(_env("MAX_PAGES", "2")))
    min_chars: int = field(default_factory=lambda: int(_env("MIN_CHARS", "50")))
    sync_pdf_limit: int = field(default_factory=lambda: int(_env("SYNC_PDF_LIMIT_BYTES", str(4 * 1024 * 1024))))


def settings() -> Settings:
    return Settings()
