# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
import time
from typing import Any, Callable, Protocol, Sequence, runtime_checkable


class EndpointThrottled(Exception):
    pass


class EndpointTimeout(Exception):
    pass


class EndpointFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@runtime_checkable
class EndpointClient(Protocol):
    def invoke(self, body: bytes) -> bytes: ...


_THROTTLE_CODES = {"ThrottlingException", "Throttling", "TooManyRequestsException"}


class SageMakerRuntimeClient:
    def __init__(self, endpoint_name: str, endpoint_timeout_ms: int = 8000, region: str | None = None,
                 boto_client: Any = None):
        self.endpoint_name = endpoint_name
        if boto_client is None:
            import boto3
            from botocore.config import Config
            boto_client = boto3.client(
                "sagemaker-runtime",
                region_name=region,
                config=Config(connect_timeout=2, read_timeout=endpoint_timeout_ms / 1000,
                              retries={"mode": "standard", "total_max_attempts": 1}),
            )
        self._client = boto_client

    def invoke(self, body: bytes) -> bytes:
        from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError
        try:
            resp = self._client.invoke_endpoint(EndpointName=self.endpoint_name, ContentType="application/json",
                                                Accept="application/json", Body=body)
        except (ReadTimeoutError, ConnectTimeoutError) as e:
            raise EndpointTimeout() from e
        except ClientError as e:
            err = e.response.get("Error", {})
            code = str(err.get("Code", "Unknown"))
            status = int(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) or 0)
            if code in _THROTTLE_CODES or status == 429:
                raise EndpointThrottled() from e
            raise EndpointFailure(code) from e
        payload = resp["Body"]
        return payload.read() if hasattr(payload, "read") else bytes(payload)


class FakeEndpoint:
    def __init__(self, responses: Sequence[Any] | None = None, raises: Sequence[Exception | None] | None = None,
                 sleeps: float = 0.0, respond: Callable[[dict], Any] | None = None):
        self.responses = list(responses or [])
        self.raises = list(raises or [])
        self.sleeps = sleeps
        self.respond = respond
        self.requests: list[dict] = []
        self.raw_requests: list[bytes] = []

    @property
    def calls(self) -> int:
        return len(self.raw_requests)

    def invoke(self, body: bytes) -> bytes:
        self.raw_requests.append(body)
        request = json.loads(body)
        self.requests.append(request)
        if self.sleeps:
            time.sleep(self.sleeps)
        if self.raises:
            err = self.raises.pop(0)
            if err is not None:
                raise err
        if self.respond is not None:
            out = self.respond(request)
        elif self.responses:
            out = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        else:
            raise EndpointFailure("NoResponseConfigured")
        if isinstance(out, bytes):
            return out
        if isinstance(out, str):
            return out.encode("utf-8")
        return json.dumps(out).encode("utf-8")
