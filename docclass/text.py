# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import hashlib
import unicodedata

from docclass.core.types import DocumentText


def normalise_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).lower().split())


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prepare(doc: DocumentText, max_pages: int = 2) -> str:
    text = normalise_text(" ".join(p.text for p in doc.pages[:max_pages]))
    return " ".join(t for t in text.split() if not t.isdigit())
