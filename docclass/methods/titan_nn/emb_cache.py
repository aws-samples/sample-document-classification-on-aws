# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from typing import Any, Protocol

import numpy as np

from docclass.text import sha256_text


def embedding_key(namespace: str, model_id: str, dimensions: int, prepared: str) -> str:
    return f"emb#{namespace}#{model_id}#{dimensions}#{sha256_text(prepared)}"


class EmbeddingCache(Protocol):
    def get(self, key: str) -> np.ndarray | None: ...

    def put(self, key: str, vec: np.ndarray) -> None: ...


class InMemoryEmbeddingCache:
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}

    def get(self, key: str) -> np.ndarray | None:
        raw = self.items.get(key)
        return None if raw is None else np.frombuffer(raw, dtype=np.float32).copy()

    def put(self, key: str, vec: np.ndarray) -> None:
        self.items[key] = np.asarray(vec, dtype=np.float32).tobytes()


class DynamoDBEmbeddingCache:
    def __init__(self, table_name: str, client: Any = None, *, key_attr: str = "pk", value_attr: str = "vec"):
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client("dynamodb", config=Config(connect_timeout=0.2, read_timeout=0.2,
                                                            retries={"mode": "standard", "total_max_attempts": 2}))
        self.client = client
        self.table_name = table_name
        self.key_attr = key_attr
        self.value_attr = value_attr

    def get(self, key: str) -> np.ndarray | None:
        resp = self.client.get_item(TableName=self.table_name, Key={self.key_attr: {"S": key}},
                                    ProjectionExpression="#v", ExpressionAttributeNames={"#v": self.value_attr})
        item = resp.get("Item")
        if not item or self.value_attr not in item:
            return None
        return np.frombuffer(item[self.value_attr]["B"], dtype=np.float32).copy()

    def put(self, key: str, vec: np.ndarray) -> None:
        self.client.put_item(TableName=self.table_name, Item={
            self.key_attr: {"S": key},
            self.value_attr: {"B": np.asarray(vec, dtype=np.float32).tobytes()},
        })
