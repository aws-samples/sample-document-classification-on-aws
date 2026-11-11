# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
import threading
import time
from typing import Any


class NamespaceNotConfigured(LookupError):
    """No configuration is available to this caller yet, for example during a
    gradual rollout. The document workflow retries it by name."""


def _not_found(e: Exception) -> bool:
    return getattr(e, "response", {}).get("Error", {}).get("Code") in ("ResourceNotFoundException", "BadRequestException")


class ConfigProvider:
    def __init__(self, client: Any, application: str, min_poll_s: int = 15):
        self.client = client
        self.application = application
        self.min_poll_s = min_poll_s
        self._state: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, namespace: str) -> dict:
        with self._lock:
            st = self._state.get(namespace)
            now = time.time()
            if st and now < st["next_poll"]:
                return st["config"]
            try:
                if not st or not st.get("token"):
                    resp = self.client.start_configuration_session(
                        ApplicationIdentifier=self.application,
                        EnvironmentIdentifier=namespace,
                        ConfigurationProfileIdentifier=namespace,
                        RequiredMinimumPollIntervalInSeconds=self.min_poll_s,
                    )
                    st = {"token": resp["InitialConfigurationToken"], "config": st.get("config") if st else None,
                          "next_poll": 0.0}
                resp = self.client.get_latest_configuration(ConfigurationToken=st["token"])
            except Exception as e:
                if not _not_found(e):
                    raise
                # Start a fresh session next time: one opened mid-rollout may not be in
                # the cohort yet, and an expired token cannot be polled again.
                cached = st.get("config") if st else None
                if cached is None:
                    self._state.pop(namespace, None)
                    raise NamespaceNotConfigured(f"no configuration deployed for namespace {namespace}") from e
                self._state[namespace] = {"config": cached, "next_poll": now + self.min_poll_s}
                return cached
            st["token"] = resp["NextPollConfigurationToken"]
            body = resp.get("Configuration")
            raw = body.read() if hasattr(body, "read") else body
            if raw:
                st["config"] = json.loads(raw)
            st["next_poll"] = now + max(int(resp.get("NextPollIntervalInSeconds", self.min_poll_s)), self.min_poll_s)
            self._state[namespace] = st
            if st["config"] is None:
                raise NamespaceNotConfigured(f"no configuration deployed for namespace {namespace}")
            return st["config"]


class StaticConfigProvider:
    def __init__(self, configs: dict[str, dict]):
        self.configs = configs

    def get(self, namespace: str) -> dict:
        if namespace not in self.configs:
            raise LookupError(f"no configuration deployed for namespace {namespace}")
        return self.configs[namespace]
