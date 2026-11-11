# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from docclass.core.types import DocumentText
from docclass.text import normalise_text, prepare

__all__ = ["char_wb_ngrams", "normalise_text", "prepare", "prepare_text"]


def prepare_text(doc: DocumentText, max_pages: int = 2) -> str:
    return prepare(doc, max_pages)


def char_wb_ngrams(text: str, min_n: int = 3, max_n: int = 5) -> list[str]:
    grams: list[str] = []
    for word in text.split():
        w = f" {word} "
        w_len = len(w)
        for n in range(min_n, max_n + 1):
            offset = 0
            grams.append(w[offset:offset + n])
            while offset + n < w_len:
                offset += 1
                grams.append(w[offset:offset + n])
            if offset == 0:
                break
    return grams
