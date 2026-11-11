# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
import zlib
from typing import Any, Protocol, runtime_checkable

import numpy as np

TITAN_V2 = "amazon.titan-embed-text-v2:0"
ALLOWED_DIMENSIONS = (256, 512, 1024)
THROTTLE_CODES = {"ThrottlingException", "ServiceQuotaExceededException", "TooManyRequestsException"}


class EmbeddingUnavailable(Exception):
    def __init__(self, cause: str):
        super().__init__(cause)
        self.cause = cause


@runtime_checkable
class Embedder(Protocol):
    model_id: str
    dimensions: int

    def embed(self, text: str) -> np.ndarray: ...


def unit(vec: np.ndarray) -> np.ndarray:
    vec = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(vec))
    if n == 0.0:
        out = np.zeros_like(vec)
        out[0] = 1.0
        return out
    return vec / n


class FakeEmbedder:
    def __init__(self, dimensions: int = 256, model_id: str = "fake-trigram-v1"):
        self.model_id = model_id
        self.dimensions = dimensions
        self.calls = 0

    def embed(self, text: str) -> np.ndarray:
        self.calls += 1
        vec = np.zeros(self.dimensions, dtype=np.float32)
        padded = f"  {text}  "
        for i in range(len(padded) - 2):
            vec[zlib.crc32(padded[i:i + 3].encode("utf-8")) % self.dimensions] += 1.0
        return unit(vec)


class BedrockTitanEmbedder:
    def __init__(self, client: Any = None, *, model_id: str = TITAN_V2, dimensions: int = 512,
                 timeout_ms: int = 2000, max_retries: int = 2, region_name: str | None = None):
        if dimensions not in ALLOWED_DIMENSIONS:
            raise ValueError(f"dimensions must be one of {ALLOWED_DIMENSIONS}")
        self.model_id = model_id
        self.dimensions = dimensions
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client("bedrock-runtime", region_name=region_name, config=Config(
                connect_timeout=max(timeout_ms / 1000.0, 0.1),
                read_timeout=max(timeout_ms / 1000.0, 0.1),
                retries={"mode": "standard", "total_max_attempts": max_retries + 1},
            ))
        self.client = client

    def embed(self, text: str) -> np.ndarray:
        from botocore.exceptions import (
            ClientError,
            ConnectTimeoutError,
            EndpointConnectionError,
            ReadTimeoutError,
        )
        body = json.dumps({"inputText": text, "dimensions": self.dimensions, "normalize": True})
        try:
            resp = self.client.invoke_model(modelId=self.model_id, body=body,
                                            contentType="application/json", accept="application/json")
        except (ReadTimeoutError, ConnectTimeoutError):
            raise EmbeddingUnavailable("timeout")
        except EndpointConnectionError:
            raise EmbeddingUnavailable("error: EndpointConnectionError")
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "ClientError")
            raise EmbeddingUnavailable("throttled" if code in THROTTLE_CODES else f"error: {code}")
        payload = resp["body"].read() if hasattr(resp["body"], "read") else resp["body"]
        vec = np.asarray(json.loads(payload)["embedding"], dtype=np.float32)
        if vec.shape != (self.dimensions,):
            raise EmbeddingUnavailable(f"error: unexpected dimension {vec.shape}")
        return unit(vec)
