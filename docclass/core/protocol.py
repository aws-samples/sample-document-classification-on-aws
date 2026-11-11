# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from typing import Protocol, runtime_checkable

from docclass.core.types import CascadeResult, ClassDecision, DocumentText


@runtime_checkable
class Classifier(Protocol):
    name: str

    def classify(self, doc: DocumentText) -> ClassDecision: ...


class PostHook(Protocol):
    def after(self, result: CascadeResult, doc: DocumentText) -> None: ...
